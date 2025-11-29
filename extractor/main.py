from fastapi import FastAPI, BackgroundTasks
import requests
from pymongo import MongoClient
import os
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import sys
import redis
import json
import threading
from urllib.parse import quote

load_dotenv()

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
    """
    Logs a message to stdout and flushes the buffer to ensure immediate output
    in Docker logs.

    Args:
        msg (str): The message to log.
    """
    print(msg)
    sys.stdout.flush()


def riot_get(url, timeout=10):
    """
    Performs a GET request to the Riot API with error handling and rate limit retries.

    Args:
        url (str): The Riot API URL to request.
        timeout (int, optional): Request timeout in seconds. Defaults to 10.

    Returns:
        dict | list | None: The JSON response if successful, otherwise None.
    """
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 429:
            log("⏳ Rate Limit (429). Sleeping 2min...")
            time.sleep(120)
            return riot_get(url, timeout)
        if r.status_code == 200: return r.json()

        if r.status_code >= 400:
            log(f"⚠ API Error {r.status_code}: {url}")
            try:
                log(f"   Reason: {r.text}")
            except Exception:
                pass

    except Exception as e:
        log(f"⚠ Request Exception: {e}")
    return None


def get_region_and_platform(name_tag, db_region=None):
    """
    Determines the routing values (Platform and Region) for a summoner.
    Prioritizes DB stored region if available, otherwise infers from the tag line.

    Args:
        name_tag (str): The full Summoner Name (Name#Tag).
        db_region (str, optional): The region currently stored in the DB.

    Returns:
        tuple: A tuple containing (platform_id, region_routing).
               Example: ("euw1", "europe").
    """
    # 1. Use DB info if available (Auto-Discovery result)
    if db_region and db_region in PLATFORM_TO_REGION.values():
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

    return "euw1", "europe"


def auto_detect_correct_region(puuid, current_platform):
    """
    Attempts to find a user across all supported platforms if the default platform fails.
    If found, updates the database with the correct region and platform.

    Args:
        puuid (str): The user's PUUID.
        current_platform (str): The platform ID that failed.

    Returns:
        tuple: (new_platform, new_region) if found, otherwise (None, None).
    """
    log(f"Auto-detecting region for PUUID {puuid[:10]}...")

    platforms_to_try = [p for p in PLATFORM_TO_REGION.keys() if p != current_platform]

    for plt in platforms_to_try:
        url = f"https://{plt}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}"
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                new_reg = PLATFORM_TO_REGION[plt]
                log(f"Found user on {plt} ({new_reg})!")
                db.summoners.update_one({"puuid": puuid}, {"$set": {"region": new_reg, "platform": plt}})
                return plt, new_reg
        except Exception:
            pass
        time.sleep(0.1)

    log("Could not find user on ANY region.")
    return None, None


def get_local_puuid(name, tag):
    """
    Resolves the PUUID for a given Name#Tag using the current API Key.
    This is used to handle '400 Exception Decrypting' errors caused by key rotation
    or multiple applications.

    Args:
        name (str): The Game Name.
        tag (str): The Tag Line.

    Returns:
        str | None: The PUUID valid for the current API key, or None if failed.
    """
    log(f"Translating PUUID for {name}#{tag} to match current API Key...")

    safe_name = quote(name)
    safe_tag = quote(tag)
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{safe_name}/{safe_tag}?api_key={RIOT_API_KEY}"

    data = riot_get(url)
    if data and "puuid" in data:
        return data["puuid"]
    return None


def update_basic_summoner_info(puuid, platform, name):
    """
    Updates the summoner's basic profile information (level, icon, encrypted ID) in the DB.
    Handles automatic region detection and key mismatch scenarios.

    Args:
        puuid (str): The PUUID currently stored in the DB.
        platform (str): The platform ID (e.g., 'euw1').
        name (str): The summoner's full name (for logging/recovery).

    Returns:
        tuple: (success (bool), encrypted_summoner_id (str|None)).
    """
    url = f"https://{platform}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}"

    # 1. Try Standard Request
    r = requests.get(url)

    # 2. HANDLE KEY MISMATCH (The Fix)
    if r.status_code == 400 and "Exception decrypting" in r.text:
        log(f"⚠️ Profile Key Mismatch for {name}. Fetching local ID...")

        if "#" in name:
            g_name, t_line = name.split("#")
        else:
            g_name, t_line = name, "EUW"

        # Fixed call signature (removed puuid)
        local_puuid = get_local_puuid(g_name, t_line)

        if local_puuid:
            url = f"https://{platform}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{local_puuid}?api_key={RIOT_API_KEY}"
            r = requests.get(url)

    # 3. Handle Auto-Detect
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
        }
        db.summoners.update_one({"puuid": puuid}, {"$set": update_data})
        return True, data.get("id")

    return False, None


def update_db_rank_data(puuid, solo_data):
    """
    Updates the summoner's Ranked Solo queue statistics in the database.

    Args:
        puuid (str): The summoner's PUUID.
        solo_data (dict): The dictionary containing rank information from Riot API.

    Returns:
        bool: Always True.
    """
    if not solo_data: solo_data = {}
    rank_data = {
        "last_rank_update": datetime.now(timezone.utc),
        "solo_tier": solo_data.get('tier', "UNRANKED"),
        "solo_rank": solo_data.get('rank', ""),
        "solo_lp": solo_data.get('leaguePoints', 0),
        "solo_wins": solo_data.get('wins', 0),
        "solo_losses": solo_data.get('losses', 0),
    }
    db.summoners.update_one({"puuid": puuid}, {"$set": rank_data})
    if solo_data.get('tier'):
        rank_display = f"{solo_data.get('tier')} {solo_data.get('rank', '')}"
        log(f"RANK OK: {rank_display}")
    return True


def fetch_and_update_rank_fast(enc_id, platform, puuid, _name):
    """
    Attempts to fetch rank data using the encrypted summoner ID (League-V4 endpoint).

    Args:
        enc_id (str): The Encrypted Summoner ID.
        platform (str): The platform ID (e.g., 'euw1').
        puuid (str): The summoner's PUUID.
        _name (str): Unused parameter (name).

    Returns:
        bool: True if data was found and updated, False otherwise.
    """
    league_url = f"https://{platform}.api.riotgames.com/lol/league/v4/entries/by-summoner/{enc_id}?api_key={RIOT_API_KEY}"
    data = riot_get(league_url)
    if data is not None:
        solo = next((l for l in data if l['queueType'] == 'RANKED_SOLO_5x5'), None)
        if solo or not data: return update_db_rank_data(puuid, solo)
    return False


def fetch_rank_advanced(puuid, platform, name):
    """
    Fallback method to find high-elo players by iterating through Challenger/GM/Master lists
    if the direct lookup fails.

    Args:
        puuid (str): The summoner's PUUID.
        platform (str): The platform ID.
        name (str): The summoner's name.

    Returns:
        bool: Always True.
    """
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
                    log(f"FOUND in {tier}: {name}")
                    return True
    update_db_rank_data(puuid, {'tier': 'UNRANKED'})
    return True


# --- BATCH LOGIC ---
def run_batch_extraction(puuid, start, count, update_profile=False):
    """
    Executes the core extraction logic for a specific batch of matches.
    Handles profile updates, PUUID translation (Key Mismatch), and downloading match data.

    Args:
        puuid (str): The DB PUUID (Master PUUID) of the user.
        start (int): The match history start index.
        count (int): The number of matches to fetch.
        update_profile (bool, optional): Whether to also update summoner profile/rank. Defaults to False.
    """
    # 1. Get Basic Info from DB using the ORIGINAL PUUID (DB Key)
    summ = db.summoners.find_one({"puuid": puuid})
    if not summ:
        log(f"User not found in DB: {puuid}")
        return

    full_name = summ.get("summonerName", "Unknown#Unknown")
    if "#" in full_name:
        game_name, tag_line = full_name.split("#")
    else:
        game_name, tag_line = full_name, "EUW"

    db_region = summ.get("region")
    db_platform = summ.get("platform")

    if db_platform and db_region:
        platform, region = db_platform, db_region
    else:
        platform, region = get_region_and_platform(full_name)

    # --- PUUID CONTEXT SWITCHING ---
    working_puuid = puuid

    # 2. Update Profile & Rank (ONLY IF REQUESTED)
    if update_profile:
        log(f"Checking Profile: {full_name}")
        is_ok, fetched_id = update_basic_summoner_info(puuid, platform, full_name)

        # Refetch just in case region changed
        summ = db.summoners.find_one({"puuid": puuid})
        if summ.get("platform"): platform = summ.get("platform")

        saved_id = summ.get("encryptedSummonerId") or fetched_id

        rank_updated = False
        if saved_id: rank_updated = fetch_and_update_rank_fast(saved_id, platform, puuid, full_name)
        if not rank_updated: fetch_rank_advanced(puuid, platform, full_name)

    # 3. Fetch Matches (Specific Batch)
    log(f"{full_name}: Fetching batch {start}-{start + count} (Region: {region})...")

    ids_url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/by-puuid/{working_puuid}/ids?start={start}&count={count}&api_key={RIOT_API_KEY}"

    try:
        r = requests.get(ids_url, timeout=10)

        # CATCH THE KEY MISMATCH HERE
        if r.status_code == 400 and "Exception decrypting" in r.text:
            log(f"Key Mismatch detected for {full_name}. Resolving local PUUID...")
            # Fixed call signature (removed puuid)
            new_local_puuid = get_local_puuid(game_name, tag_line)

            if new_local_puuid:
                working_puuid = new_local_puuid
                ids_url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/by-puuid/{working_puuid}/ids?start={start}&count={count}&api_key={RIOT_API_KEY}"
                r = requests.get(ids_url, timeout=10)
            else:
                log("Failed to resolve local PUUID. Aborting batch.")
                return

        if r.status_code == 429:
            log("⏳ Rate Limit (429). Sleeping 2min...")
            time.sleep(120)
            return run_batch_extraction(puuid, start, count, update_profile)

        if r.status_code != 200:
            log(f"⚠ Match fetch failed: {r.status_code}")
            return

        match_ids = r.json()

    except Exception as e:
        log(f"⚠ Request Exception: {e}")
        return

    if not match_ids or not isinstance(match_ids, list):
        return

    new_in_batch = 0
    for match_id in match_ids:
        exists = db.matches_raw.find_one({"matchId": match_id})
        if exists: continue

        m_url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={RIOT_API_KEY}"
        data = riot_get(m_url)

        if data:
            try:
                # SAVE WITH ORIGINAL PUUID
                db.matches_raw.insert_one({
                    "matchId": match_id,
                    "puuid": puuid,
                    "raw": data,
                    "processed": False,
                    "timestamp": datetime.now(timezone.utc)
                })
                new_in_batch += 1
            except Exception:
                pass
            time.sleep(0.1)

    if new_in_batch > 0:
        log(f"{full_name}: Downloaded {new_in_batch} new matches (Batch {start}) via Extractor")


def run_extraction_job(limit=100, target_puuid=None):
    """
    Scheduled job creator. Finds users in the DB and pushes extraction tasks to Redis.
    Splits larger limits into chunks of 50 to respect rate limits.

    Args:
        limit (int, optional): Total matches to check per user. Defaults to 100.
        target_puuid (str, optional): If provided, only runs for this specific user. Defaults to None.
    """
    query = {"puuid": target_puuid} if target_puuid else {}
    users = list(db.summoners.find(query, {"puuid": 1}))

    # Batch size consistent with API Service to respect rate limits
    BATCH_SIZE = 50

    for u in users:
        # Loop through the limit in steps of BATCH_SIZE
        for start in range(0, limit, BATCH_SIZE):
            current_count = min(BATCH_SIZE, limit - start)

            redis_client.lpush("extraction_queue", json.dumps({
                "action": "extract_batch",
                "puuid": u["puuid"],
                "start": start,
                "count": current_count,
                # Only update profile on the first batch
                "update_profile": (start == 0)
            }))


# --- WORKER ---
def redis_worker():
    """
    Background worker thread that continually pulls tasks from the 'extraction_queue'
    in Redis and executes them using run_batch_extraction.
    """
    log("Redis Worker Listening...")
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
                limit = task.get("limit", 100)
                run_extraction_job(limit)

        except Exception as e:
            log(f"Redis Worker Error: {e}")
            time.sleep(5)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Lifespan context manager for the FastAPI app.
    Handles startup (API key validation, DB indexing, starting workers/schedulers)
    and shutdown.

    Args:
        _app (FastAPI): The FastAPI application instance (unused).
    """
    log(f"Validating API Key: {RIOT_API_KEY[:5]}...")
    test_url = f"https://euw1.api.riotgames.com/lol/status/v4/platform-data?api_key={RIOT_API_KEY}"
    r = requests.get(test_url)
    if r.status_code == 200:
        log("API Key is VALID")
    else:
        log(f"API KEY INVALID: {r.status_code} - {r.text}")

    try:
        db.matches_raw.create_index("matchId", unique=True)
    except Exception:
        pass

    threading.Thread(target=redis_worker, daemon=True).start()

    scheduler = BackgroundScheduler()
    # Auto-update every 10 minutes
    scheduler.add_job(run_extraction_job, 'interval', minutes=10)
    scheduler.start()

    log("Extractor Service Ready")
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root():
    """
    Health check endpoint.

    Returns:
        dict: Status message.
    """
    return {"status": "Extractor Running"}


@app.get("/trigger_extract")
def manual_trigger(background_tasks: BackgroundTasks, count: int = 50, puuid: str = None):
    """
    Endpoint to manually trigger an extraction job via HTTP request.

    Args:
        background_tasks (BackgroundTasks): FastAPI background task handler.
        count (int, optional): Number of matches to extract. Defaults to 50.
        puuid (str, optional): Target specific user. Defaults to None (all users).

    Returns:
        dict: Status message indicating job started.
    """
    background_tasks.add_task(run_extraction_job, limit=count, target_puuid=puuid)
    return {"status": "Job started"}