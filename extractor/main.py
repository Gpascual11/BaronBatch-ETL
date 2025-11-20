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

load_dotenv()

RIOT_API_KEY = os.getenv("RIOT_API_KEY")

# Ensure we connect to the correct mongo host
mongo = MongoClient("mongodb://db:27017")
db = mongo["riot"]


def log(msg):
    print(msg)
    sys.stdout.flush()


def riot_get(url, timeout=10):
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 429:
            log("⏳ Rate Limit (429). Sleeping 2min...")
            time.sleep(120)
            return riot_get(url, timeout)  # Retry once
        if r.status_code == 200: return r.json()
    except Exception as e:
        log(f"⚠️ Riot API Error: {e}")
    return None


def get_region_and_platform(name_tag):
    if "#" in name_tag:
        tag = name_tag.split("#")[-1].upper()
    else:
        tag = "EUW"

    if tag == "KR1": return "kr", "asia"
    if tag == "NA1": return "na1", "americas"
    # Add more mappings if needed
    return "euw1", "europe"


# --- FIX: FUNCTION TO REPAIR MISSING ICONS/LEVELS ---
def update_basic_summoner_info(puuid, platform, name):
    """Updates Level and Profile Icon using Summoner-V4 if missing/outdated"""
    try:
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

    except Exception as e:
        log(f"❌ Error fetching basic info for {name}: {e}")

    return False, None


def update_db_rank_data(puuid, solo_data):
    if not solo_data:
        solo_data = {}

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
    try:
        league_url = f"https://{platform}.api.riotgames.com/lol/league/v4/entries/by-summoner/{enc_id}?api_key={RIOT_API_KEY}"
        data = riot_get(league_url)

        if data is not None:
            solo = next((l for l in data if l['queueType'] == 'RANKED_SOLO_5x5'), None)
            if solo or not data:
                return update_db_rank_data(puuid, solo)

    except Exception as e:
        log(f"🔥 Error Rank Fast {name}: {e}")
    return False


def fetch_rank_advanced(puuid, platform, name):
    tiers_high_elo = ["CHALLENGER", "GRANDMASTER", "MASTER"]
    log(f"🔍 [EXP-V4] Checking High Elo for {name}...")

    for tier in tiers_high_elo:
        url = f"https://{platform}.api.riotgames.com/lol/league/v4/{tier.lower()}leagues/by-queue/RANKED_SOLO_5x5?api_key={RIOT_API_KEY}"
        # Challenger/GM/Master endpoint specifics can be handled by exact URL if needed,
        # but commonly v4/{tier}leagues works or specific named endpoints.
        # Using specific endpoints for safety:
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


def run_extraction_job(limit=50):
    log(f"⏰ [AUTO] Extraction Cycle Started ({limit} matches)")
    if not RIOT_API_KEY:
        log("❌ API KEY MISSING")
        return

    try:
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

        # 1. --- REPAIR STEP: Fix Icon & Level ---
        # This guarantees the icon/level is fetched even if API failed before
        _, fetched_id = update_basic_summoner_info(puuid, platform, name)
        if not saved_id and fetched_id:
            saved_id = fetched_id

        # 2. Update Rank
        rank_updated = False
        if saved_id:
            rank_updated = fetch_and_update_rank_fast(saved_id, platform, puuid, name)
        if not rank_updated:
            fetch_rank_advanced(puuid, platform, name)

        # 3. Download Matches
        ids_url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?count={limit}&api_key={RIOT_API_KEY}"
        try:
            r = requests.get(ids_url, timeout=10)
            if r.status_code == 200:
                match_ids = r.json()
                new_c = 0
                for match_id in match_ids:
                    exists = db.matches_raw.find_one({"matchId": match_id})
                    if exists: continue

                    m_url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={RIOT_API_KEY}"
                    raw_r = requests.get(m_url, timeout=10)

                    if raw_r.status_code == 200:
                        data = raw_r.json()
                        db.matches_raw.insert_one({
                            "matchId": match_id, "puuid": puuid,
                            "raw": data, "processed": False,
                            "timestamp": datetime.utcnow()
                        })
                        new_c += 1
                        time.sleep(0.1)  # Be nice to API

                if new_c > 0: log(f"📥 {name}: +{new_c} new matches")
        except Exception as e:
            log(f"❌ Error downloading matches for {name}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_extraction_job, 'interval', minutes=30, kwargs={"limit": 50})
    scheduler.start()
    log("🚀 Extractor Started (v5)")
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root(): return {"status": "Extractor Running"}


@app.get("/trigger_extract")
def manual_trigger(background_tasks: BackgroundTasks, count: int = 50):
    background_tasks.add_task(run_extraction_job, limit=count)
    return {"status": "Job started"}