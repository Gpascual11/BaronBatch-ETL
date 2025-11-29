from fastapi import FastAPI, BackgroundTasks
import requests
from pymongo import MongoClient
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import sys
import redis
import json
import threading

load_dotenv()

RIOT_API_KEY = os.getenv("RIOT_API_KEY")

mongo = MongoClient("mongodb://db:27017")
db = mongo["riot"]

# --- REDIS CONNECTION ---
redis_client = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)


def log(msg):
    print(msg)
    sys.stdout.flush()


def riot_get(url, timeout=10):
    """Robust GET request with Rate Limit handling"""
    try:
        r = requests.get(url, timeout=timeout)

        if r.status_code == 429:
            log("⏳ Rate Limit (429). Sleeping 2min...")
            time.sleep(120)
            return riot_get(url, timeout)  # Retry

        if r.status_code == 200:
            return r.json()

        # Log other errors so we know why it failed
        if r.status_code >= 400:
            log(f"⚠️ API Error {r.status_code}: {url}")

    except Exception as e:
        log(f"⚠️ Request Exception: {e}")
    return None


def get_region_and_platform(name_tag):
    if "#" in name_tag:
        tag = name_tag.split("#")[-1].upper()
    else:
        tag = "EUW"

    if tag == "KR1": return "kr", "asia"
    if tag == "NA1": return "na1", "americas"
    return "euw1", "europe"


def update_basic_summoner_info(puuid, platform, name):
    url = f"https://{platform}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}"
    data = riot_get(url)

    if data:
        update_data = {
            "summonerLevel": data.get("summonerLevel", 0),
            "profileIconId": data.get("profileIconId", 29),
            "encryptedSummonerId": data.get("id")
        }
        db.summoners.update_one({"puuid": puuid}, {"$set": update_data})
        return True, data.get("id")
    return False, None


def update_db_rank_data(puuid, solo_data):
    if not solo_data: solo_data = {}

    rank_data = {
        "last_rank_update": datetime.utcnow(),
        "solo_tier": solo_data.get('tier', "UNRANKED"),
        "solo_rank": solo_data.get('rank', ""),
        "solo_lp": solo_data.get('leaguePoints', 0),
        "solo_wins": solo_data.get('wins', 0),
        "solo_losses": solo_data.get('losses', 0),
    }
    db.summoners.update_one({"puuid": puuid}, {"$set": rank_data})

    if solo_data.get('tier'):
        rank_display = f"{solo_data.get('tier')} {solo_data.get('rank', '')}"
        log(f"🏅 RANG OK: {rank_display}")
    return True


def fetch_and_update_rank_fast(enc_id, platform, puuid, name):
    league_url = f"https://{platform}.api.riotgames.com/lol/league/v4/entries/by-summoner/{enc_id}?api_key={RIOT_API_KEY}"
    data = riot_get(league_url)

    if data is not None:
        solo = next((l for l in data if l['queueType'] == 'RANKED_SOLO_5x5'), None)
        if solo or not data:
            return update_db_rank_data(puuid, solo)
    return False


def fetch_rank_advanced(puuid, platform, name):
    tiers_high_elo = ["CHALLENGER", "GRANDMASTER", "MASTER"]
    log(f"🔍 [EXP-V4] Checking High Elo for {name}...")

    for tier in tiers_high_elo:
        url = f"https://{platform}.api.riotgames.com/lol/league/v4/{tier.lower()}leagues/by-queue/RANKED_SOLO_5x5?api_key={RIOT_API_KEY}"
        if tier == "CHALLENGER":
            url = f"https://{platform}.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5?api_key={RIOT_API_KEY}"
        elif tier == "GRANDMASTER":
            url = f"https://{platform}.api.riotgames.com/lol/league/v4/grandmasterleagues/by-queue/RANKED_SOLO_5x5?api_key={RIOT_API_KEY}"
        elif tier == "MASTER":
            url = f"https://{platform}.api.riotgames.com/lol/league/v4/masterleagues/by-queue/RANKED_SOLO_5x5?api_key={RIOT_API_KEY}"

        data = riot_get(url)
        if data and 'entries' in data:
            for entry in data['entries']:
                if entry.get("puuid") == puuid:
                    update_data = {
                        'tier': tier,
                        'rank': entry.get('rank', 'I'),
                        'leaguePoints': entry.get('leaguePoints', 0),
                        'wins': entry.get('wins', 0),
                        'losses': entry.get('losses', 0)
                    }
                    update_db_rank_data(puuid, update_data)
                    log(f"🎉 FOUND in {tier}: {name}")
                    return True

    log(f"⚠️ {name} not in High Elo. Setting UNRANKED.")
    update_db_rank_data(puuid, {'tier': 'UNRANKED'})
    return True


def run_extraction_job(limit=50, target_puuid=None):
    log(f"⏰ Extraction Job Started (Target: {limit} matches)")
    if not RIOT_API_KEY:
        log("❌ API KEY MISSING")
        return

    try:
        if target_puuid:
            target_user = db.summoners.find_one({"puuid": target_puuid})
            raw_summoners = [target_user] if target_user else []
            log(f"🎯 Targeting single user: {target_puuid}")
        else:
            raw_summoners = list(db.summoners.find({}))
    except:
        log("❌ DB Connection Error")
        return

    unique_summoners = {s['puuid']: s for s in raw_summoners}.values()

    for summ in unique_summoners:
        puuid = summ.get("puuid")
        name = summ.get("summonerName")
        saved_id = summ.get("encryptedSummonerId")
        platform, region = get_region_and_platform(name)

        # 1. Repair Icon/Level
        _, fetched_id = update_basic_summoner_info(puuid, platform, name)
        if not saved_id and fetched_id: saved_id = fetched_id

        # 2. Update Rank
        rank_updated = False
        if saved_id:
            rank_updated = fetch_and_update_rank_fast(saved_id, platform, puuid, name)
        if not rank_updated:
            fetch_rank_advanced(puuid, platform, name)

        # 3. Download Matches (With Pagination for >100)
        fetched_count = 0
        start_index = 0

        # Loop until we have enough matches
        while fetched_count < limit:
            # Riot Max is 100 per request
            batch_size = min(100, limit - fetched_count)

            ids_url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start={start_index}&count={batch_size}&api_key={RIOT_API_KEY}"

            # Use robust getter
            match_ids = riot_get(ids_url)

            if not match_ids or not isinstance(match_ids, list):
                break

            new_in_batch = 0
            for match_id in match_ids:
                exists = db.matches_raw.find_one({"matchId": match_id})
                if exists: continue

                m_url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={RIOT_API_KEY}"
                data = riot_get(m_url)  # Use robust getter

                if data:
                    try:
                        db.matches_raw.insert_one({
                            "matchId": match_id, "puuid": puuid,
                            "raw": data, "processed": False,
                            "timestamp": datetime.utcnow()
                        })
                        new_in_batch += 1
                    except:
                        pass
                    time.sleep(0.1)  # Be nice to API

            if new_in_batch > 0:
                log(f"📥 {name}: +{new_in_batch} matches (Batch {start_index}-{start_index + batch_size})")

            fetched_count += len(match_ids)
            start_index += len(match_ids)

            # If we got fewer matches than asked, we reached the end of history
            if len(match_ids) < batch_size:
                break


# --- REDIS WORKER ---
def redis_worker():
    log("👂 Redis Worker Listening...")
    while True:
        try:
            _, data = redis_client.blpop("extraction_queue")
            task = json.loads(data)
            action = task.get("action")
            limit = task.get("limit", 50)

            if action == "extract":
                puuid = task.get("puuid")
                log(f"📨 Task: Extract {puuid} (limit={limit})")
                run_extraction_job(limit=limit, target_puuid=puuid)

            elif action == "refresh_all":
                log(f"📨 Task: Refresh ALL (limit={limit})")
                run_extraction_job(limit=limit)

        except Exception as e:
            log(f"❌ Redis Worker Error: {e}")
            time.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db.matches_raw.create_index("matchId", unique=True)
    except:
        pass

    threading.Thread(target=redis_worker, daemon=True).start()

    scheduler = BackgroundScheduler()
    scheduler.add_job(run_extraction_job, 'interval', minutes=10, kwargs={"limit": 100})
    scheduler.start()

    log("🚀 Extractor Service Ready")
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root(): return {"status": "Extractor Running"}


@app.get("/trigger_extract")
def manual_trigger(background_tasks: BackgroundTasks, count: int = 50, puuid: str = None):
    background_tasks.add_task(run_extraction_job, limit=count, target_puuid=puuid)
    return {"status": "Job started"}