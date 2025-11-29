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
from urllib.parse import quote

load_dotenv()

# --- FIX: STRIP KEY ---
RIOT_API_KEY = os.getenv("RIOT_API_KEY", "").strip()

mongo = MongoClient("mongodb://db:27017")
db = mongo["riot"]

redis_client = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)

# --- REGION MAPPING FOR AUTO-DISCOVERY ---
# Maps platform ID to the routing value for Match-V5
PLATFORM_TO_REGION = {
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe",
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
    "kr": "asia", "jp1": "asia",
    "oc1": "sea", "ph2": "sea", "sg2": "sea", "th2": "sea", "tw2": "sea", "vn2": "sea"
}


def log(msg):
    print(msg)
    sys.stdout.flush()


def riot_get(url, timeout=10):
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 429:
            log("⏳ Rate Limit (429). Sleeping 2min...")
            time.sleep(120)
            return riot_get(url, timeout)
        if r.status_code == 200: return r.json()

        # --- DEBUG: PRINT RESPONSE BODY ---
        if r.status_code >= 400:
            log(f"⚠️ API Error {r.status_code}: {url}")
            try:
                log(f"   Reason: {r.text}")
            except:
                pass

    except Exception as e:
        log(f"⚠️ Request Exception: {e}")
    return None


def get_region_and_platform(name_tag, db_region=None):
    """
    Returns (Platform, Region)
    Prioritizes DB stored region if available.
    """
    # 1. Use DB info if available (Auto-Discovery result)
    if db_region and db_region in PLATFORM_TO_REGION.values():
        # Heuristic: If DB says 'americas', default to 'na1'. If 'europe', default 'euw1'
        # This isn't perfect for platform (e.g. EUW vs EUNE) but Match-V5 only cares about Region.
        # For Summoner-V4 (Platform specific), we might need to store platform too.
        # For now, let's trust the input tag if it matches the region, otherwise default.
        if db_region == "europe": return "euw1", "europe"
        if db_region == "americas": return "na1", "americas"
        if db_region == "asia": return "kr", "asia"

    # 2. Fallback to Tag heuristics
    if not name_tag: return "euw1", "europe"

    # Normalize tag
    if "#" in name_tag:
        tag = name_tag.split("#")[-1].upper()
    else:
        tag = "EUW"

    if tag == "KR1": return "kr", "asia"
    if tag == "NA1": return "na1", "americas"
    if tag == "TR1": return "tr1", "europe"
    if tag == "EUN1": return "eun1", "europe"
    if tag == "BR1": return "br1", "americas"
    if tag == "JP1": return "jp1", "asia"
    if tag == "LAN": return "la1", "americas"
    if tag == "LAS": return "la2", "americas"
    if tag == "OCE": return "oc1", "sea"

    return "euw1", "europe"  # Default


def auto_detect_correct_region(puuid, current_platform):
    """
    If EUW fails, try ALL other platforms to find the user.
    Returns: (new_platform, new_region) or None
    """
    log(f"🕵️ Auto-detecting region for PUUID {puuid[:10]}...")

    # List of platforms to probe (exclude the one we already tried)
    platforms_to_try = [p for p in PLATFORM_TO_REGION.keys() if p != current_platform]

    for plt in platforms_to_try:
        url = f"https://{plt}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}"
        # We use requests directly to avoid logging 400 errors during probing
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                new_reg = PLATFORM_TO_REGION[plt]
                log(f"✅ Found user on {plt} ({new_reg})!")
                # Update DB immediately
                db.summoners.update_one({"puuid": puuid}, {"$set": {"region": new_reg, "platform": plt}})
                return plt, new_reg
        except:
            pass
        time.sleep(0.1)  # Be nice

    log("❌ Could not find user on ANY region.")
    return None, None


# In extractor/main.py

def update_basic_summoner_info(puuid, platform, name):
    url = f"https://{platform}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}"

    # 1. Try Standard Request
    r = requests.get(url)

    # 2. HANDLE KEY MISMATCH (The Fix)
    if r.status_code == 400 and "Exception decrypting" in r.text:
        log(f"⚠️ Profile Key Mismatch for {name}. Fetching local ID...")

        # Extract Name/Tag from "Name#Tag"
        if "#" in name:
            g_name, t_line = name.split("#")
        else:
            g_name, t_line = name, "EUW"

        local_puuid = get_local_puuid(puuid, g_name, t_line)

        if local_puuid:
            # Retry with correct Local PUUID
            url = f"https://{platform}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{local_puuid}?api_key={RIOT_API_KEY}"
            r = requests.get(url)

    # 3. Handle Auto-Detect (Only if it's NOT a decryption error)
    if r.status_code == 404:
        new_plt, new_reg = auto_detect_correct_region(puuid, platform)
        if new_plt:
            url = f"https://{new_plt}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}"
            r = requests.get(url)

    data = r.json() if r.status_code == 200 else None

    if data:
        update_data = {
            "summonerLevel": data.get("summonerLevel", 0),
            "profileIconId": data.get("profileIconId", 29),
            # Important: We do NOT update 'encryptedSummonerId' here because
            # it might be encrypted for Key #2, but we want the DB to look like Key #1.
            # However, for 'fetch_rank' to work in this specific container, we return the ID.
        }
        # Only update DB non-sensitive fields
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
        if solo or not data: return update_db_rank_data(puuid, solo)
    return False


def fetch_rank_advanced(puuid, platform, name):
    tiers_high_elo = ["CHALLENGER", "GRANDMASTER", "MASTER"]
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
    update_db_rank_data(puuid, {'tier': 'UNRANKED'})
    return True


# --- BATCH LOGIC ---
def get_local_puuid(original_puuid, name, tag):
    """
    Helper: If the API Key cannot decrypt the PUUID (400 Error),
    we resolve the name again to get a PUUID valid for THIS API Key.
    """
    log(f"🔄 Translating PUUID for {name}#{tag} to match current API Key...")

    # 1. Use Account V1 to get the PUUID valid for this specific container's Key
    safe_name = quote(name)
    safe_tag = quote(tag)
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{safe_name}/{safe_tag}?api_key={RIOT_API_KEY}"

    data = riot_get(url)
    if data and "puuid" in data:
        return data["puuid"]
    return None


def run_batch_extraction(puuid, start, count, update_profile=False):
    # 1. Get Basic Info from DB using the ORIGINAL PUUID (DB Key)
    summ = db.summoners.find_one({"puuid": puuid})
    if not summ:
        log(f"❌ User not found in DB: {puuid}")
        return

    full_name = summ.get("summonerName", "Unknown#Unknown")
    if "#" in full_name:
        game_name, tag_line = full_name.split("#")
    else:
        game_name, tag_line = full_name, "EUW"

    # Use stored region/platform if available, else infer
    db_region = summ.get("region")
    db_platform = summ.get("platform")

    if db_platform and db_region:
        platform, region = db_platform, db_region
    else:
        platform, region = get_region_and_platform(full_name)

    # --- PUUID CONTEXT SWITCHING ---
    # We use 'working_puuid' for API calls, but 'puuid' (original) for DB storage
    working_puuid = puuid

    # 2. Update Profile & Rank (ONLY IF REQUESTED)
    if update_profile:
        log(f"👤 Checking Profile: {full_name}")
        # Note: update_basic_summoner_info might fail if PUUID doesn't match key
        # We try; if it fails, we catch it later or logic handles it.
        is_ok, fetched_id = update_basic_summoner_info(puuid, platform, full_name)

        # If is_ok is False, it might be the key mismatch.
        # But let's rely on the Match-V5 call to trigger the "400" check.

    # 3. Fetch Matches (Specific Batch)
    log(f"📥 {full_name}: Fetching batch {start}-{start + count} (Region: {region})...")

    ids_url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/by-puuid/{working_puuid}/ids?start={start}&count={count}&api_key={RIOT_API_KEY}"

    # --- HERE IS THE FIX LOGIC ---
    try:
        r = requests.get(ids_url, timeout=10)

        # CATCH THE KEY MISMATCH HERE
        if r.status_code == 400 and "Exception decrypting" in r.text:
            log(f"⚠️ Key Mismatch detected for {full_name}. Resolving local PUUID...")
            new_local_puuid = get_local_puuid(puuid, game_name, tag_line)

            if new_local_puuid:
                working_puuid = new_local_puuid
                # Retry with new working_puuid
                ids_url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/by-puuid/{working_puuid}/ids?start={start}&count={count}&api_key={RIOT_API_KEY}"
                r = requests.get(ids_url, timeout=10)
            else:
                log("❌ Failed to resolve local PUUID. Aborting batch.")
                return

        # Handle Rate limits
        if r.status_code == 429:
            log("⏳ Rate Limit (429). Sleeping 2min...")
            time.sleep(120)
            # Recursive retry
            return run_batch_extraction(puuid, start, count, update_profile)

        if r.status_code != 200:
            log(f"⚠️ Match fetch failed: {r.status_code}")
            return

        match_ids = r.json()

    except Exception as e:
        log(f"⚠️ Request Exception: {e}")
        return

    if not match_ids or not isinstance(match_ids, list):
        return

    new_in_batch = 0
    for match_id in match_ids:
        # Check if match exists
        exists = db.matches_raw.find_one({"matchId": match_id})
        if exists: continue

        m_url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={RIOT_API_KEY}"
        data = riot_get(m_url)

        if data:
            try:
                # IMPORTANT: We save using 'puuid' (The Original from DB)
                # NOT 'working_puuid' (The temporary one for this Key)
                # This ensures your Dashboard sees the matches under the correct user.
                db.matches_raw.insert_one({
                    "matchId": match_id,
                    "puuid": puuid,  # <--- Original PUUID from MongoDB
                    "raw": data,
                    "processed": False,
                    "timestamp": datetime.utcnow()
                })
                new_in_batch += 1
            except:
                pass
            time.sleep(0.1)

    if new_in_batch > 0:
        log(f"✅ {full_name}: Downloaded {new_in_batch} new matches (Batch {start}) via Extractor")


def run_extraction_job(limit=100):
    """Fallback for scheduled jobs (Auto-Update)"""
    users = list(db.summoners.find({}, {"puuid": 1}))
    for u in users:
        redis_client.lpush("extraction_queue", json.dumps({
            "action": "extract_batch",
            "puuid": u["puuid"],
            "start": 0,
            "count": 100,
            "update_profile": True
        }))


# --- WORKER ---
def redis_worker():
    log("👂 Redis Worker Listening...")
    while True:
        try:
            _, data = redis_client.blpop("extraction_queue")
            task = json.loads(data)
            action = task.get("action")

            if action == "extract_batch":
                puuid = task.get("puuid")
                start = task.get("start", 0)
                count = task.get("count", 100)
                update_p = task.get("update_profile", False)

                run_batch_extraction(puuid, start, count, update_p)

            elif action == "refresh_all":
                # Handle limit from payload
                limit = task.get("limit", 100)
                run_extraction_job(limit)

        except Exception as e:
            log(f"❌ Redis Worker Error: {e}")
            time.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log(f"🔑 Validating API Key: {RIOT_API_KEY[:5]}...")
    test_url = f"https://euw1.api.riotgames.com/lol/status/v4/platform-data?api_key={RIOT_API_KEY}"
    r = requests.get(test_url)
    if r.status_code == 200:
        log("✅ API Key is VALID")
    else:
        log(f"❌ API KEY INVALID: {r.status_code} - {r.text}")

    try:
        db.matches_raw.create_index("matchId", unique=True)
    except:
        pass

    threading.Thread(target=redis_worker, daemon=True).start()

    scheduler = BackgroundScheduler()
    scheduler.add_job(run_extraction_job, 'interval', minutes=10)
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