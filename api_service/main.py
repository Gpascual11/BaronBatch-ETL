from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient
import os
import requests
from dotenv import load_dotenv
import re
import redis
import json

load_dotenv()

RIOT_API_KEY = os.getenv("RIOT_API_KEY")
mongo = MongoClient("mongodb://db:27017", serverSelectionTimeoutMS=3000)
db = mongo["riot"]

redis_client = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Handles startup tasks. Checks Database and API connectivity.
    Uses 'Soft Fail' logic: If the internet is down, it logs a warning
    but lets the app start so it doesn't enter a crash loop.
    """
    print("API Service Starting...")
    try:
        mongo.admin.command('ping')
        print("MongoDB Connection: OK")
    except Exception as e:
        print(f"MongoDB Connection Failed: {e}")

    try:
        # Simple check to a status endpoint
        test_url = f"https://euw1.api.riotgames.com/lol/status/v4/platform-data?api_key={RIOT_API_KEY}"
        r = requests.get(test_url, timeout=5)
        if r.status_code == 200:
            print("Riot API Key: VALID")
        else:
            print(f"Riot API Key Issue: {r.status_code}")
    except Exception as e:
        print(f"Network Check Failed (Offline?): {e}")
        print("Service starting anyway. Will retry connections later.")

    yield
    print("API Service Shutting Down...")


app = FastAPI(lifespan=lifespan)


class SummonerRequest(BaseModel):
    name_tag: str


def get_routing_info(tag):
    """
    Determines the routing region based on the tag line.
    Defaults to Europe (EUW1) if not specified.

    Args:
        tag (str): The tag line (e.g., "EUW", "KR1").

    Returns:
        tuple: (region_routing, platform_id).
    """
    tag = tag.upper()
    if tag == "KR1": return "asia", "kr"
    if tag == "NA1": return "americas", "na1"
    return "europe", "euw1"


def check_db():
    """
    Health check for MongoDB connection.

    Returns:
        bool: True if DB is reachable, False otherwise.
    """
    try:
        mongo.admin.command('ping')
        return True
    except Exception:
        return False


@app.get("/summoners")
def get_summoners_list():
    """
    Retrieves a list of all tracked summoners from the database.

    Returns:
        list: Sorted list of unique summoner names (Name#Tag).
    """
    if not check_db(): return []
    try:
        summoners = list(db.summoners.find({}, {"summonerName": 1, "_id": 0}))
        return sorted(list(set([s["summonerName"] for s in summoners])))
    except Exception:
        return []


@app.post("/add_summoner")
def add_summoner(request: SummonerRequest):
    """
    Adds a new summoner to the tracking list.
    1. Verifies existence via Riot Account API.
    2. Saves basic info to MongoDB.
    3. Queues extraction tasks in Redis (split into batches of 50 to avoid rate limits).

    Args:
        request (SummonerRequest): Contains 'name_tag' (Name#Tag).

    Returns:
        dict: Success message and corrected name.
    """
    if not check_db(): raise HTTPException(503, "DB Loading...")

    full_name = request.name_tag
    if "#" not in full_name: raise HTTPException(400, "Format: Name#Tag")

    tag = full_name.split("#")[-1].strip()
    game_name = full_name.split("#")[0].strip()

    api_region, platform = get_routing_info(tag)

    acc_url = f"https://{api_region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag}?api_key={RIOT_API_KEY}"

    try:
        r = requests.get(acc_url, timeout=5)
    except Exception:
        raise HTTPException(504, "Timeout contacting Riot API")

    if r.status_code == 404: raise HTTPException(404, "Player not found (Check spelling)")
    if r.status_code == 429: raise HTTPException(429, "Riot Rate Limit (429). Please wait 2 mins.")
    if r.status_code == 403: raise HTTPException(403, "API Key Expired or Invalid (403).")
    if r.status_code != 200: raise HTTPException(400, f"Riot Error {r.status_code}: {r.text}")

    data = r.json()
    puuid = data.get("puuid")
    real_name = f"{data.get('gameName')}#{data.get('tagLine')}"

    update_data = {
        "summonerName": real_name,
        "region": api_region,
    }

    db.summoners.update_one({"puuid": puuid}, {"$set": update_data}, upsert=True)

    try:
        BATCH_SIZE = 50
        TOTAL_GAMES = 200

        for start in range(0, TOTAL_GAMES, BATCH_SIZE):
            redis_client.lpush("extraction_queue", json.dumps({
                "action": "extract_batch",
                "puuid": puuid,
                "start": start,
                "count": BATCH_SIZE,
                "update_profile": (start == 0)  # Only update profile on first batch
            }))

    except Exception as e:
        print(f"⚠ Redis Error: {e}")

    return {
        "message": f"{real_name} added! Queued parallel extraction ({TOTAL_GAMES} games).",
        "correct_name": real_name
    }


@app.delete("/summoner/{name_tag}")
def delete_summoner(name_tag: str):
    """
    Removes a summoner and all associated data (raw matches, clean matches, stats) from the DB.

    Args:
        name_tag (str): The summoner name to delete.
    """
    if not check_db(): raise HTTPException(503, "DB Down")

    clean_search = name_tag.replace(" ", "")
    if "#" in name_tag:
        parts = name_tag.split("#")
        clean_search = f"{parts[0].strip()}#{parts[1].strip()}"

    query = {"summonerName": {"$regex": f"^{re.escape(clean_search)}$", "$options": "i"}}
    summ = db.summoners.find_one(query)

    if not summ:
        raise HTTPException(404, "Summoner not found in DB")

    puuid = summ.get("puuid")
    name = summ.get("summonerName")

    db.summoners.delete_one({"puuid": puuid})
    db.matches_raw.delete_many({"puuid": puuid})
    db.matches_clean.delete_many({"puuid": puuid})
    db.aggregated_stats.delete_many({"puuid": puuid})

    return {"message": f"Deleted {name} and all data."}


@app.delete("/maintenance/cleanup")
def cleanup_data():
    """
    Performs database maintenance:
    1. Removes 'orphan' matches (matches where the user is no longer tracked).
    2. Removes duplicate raw matches.
    3. Trims match history to keep only the latest 200 games per user to save space.

    Returns:
        dict: Summary of deleted records.
    """
    if not check_db(): raise HTTPException(503, "DB Down")

    valid_puuids = [s["puuid"] for s in db.summoners.find({}, {"puuid": 1})]
    raw_res = db.matches_raw.delete_many({"puuid": {"$nin": valid_puuids}})
    # Unused variable clean_res kept for logic completeness, though not returned
    _clean_res = db.matches_clean.delete_many({"puuid": {"$nin": valid_puuids}})

    pipeline = [
        {"$group": {"_id": "$matchId", "ids": {"$push": "$_id"}, "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}}
    ]
    duplicates = list(db.matches_raw.aggregate(pipeline))
    deleted_dupes = 0
    for doc in duplicates:
        ids_to_delete = doc['ids'][1:]
        db.matches_raw.delete_many({"_id": {"$in": ids_to_delete}})
        deleted_dupes += len(ids_to_delete)

    deleted_excess = 0
    for puuid in valid_puuids:
        matches = list(db.matches_raw.find({"puuid": puuid}).sort("timestamp", -1))
        if len(matches) > 200:
            to_remove = matches[200:]
            ids = [m["_id"] for m in to_remove]
            db.matches_raw.delete_many({"_id": {"$in": ids}})
            deleted_excess += len(ids)

        c_matches = list(db.matches_clean.find({"puuid": puuid}).sort("game_timestamp", -1))
        if len(c_matches) > 200:
            c_to_remove = c_matches[200:]
            c_ids = [m["_id"] for m in c_to_remove]
            db.matches_clean.delete_many({"_id": {"$in": c_ids}})

    try:
        db.matches_raw.create_index("matchId", unique=True)
    except Exception:
        pass

    return {
        "message": "Deep Clean Successful",
        "deleted_orphans": raw_res.deleted_count,
        "deleted_duplicates": deleted_dupes,
        "trimmed_excess": deleted_excess
    }


@app.delete("/maintenance/nuke")
def nuke_database():
    """
    DANGER: Deletes ALL data in the database (Users, Matches, Stats).
    Used for factory resets.
    """
    if not check_db(): raise HTTPException(503, "DB Down")
    db.summoners.delete_many({})
    db.matches_raw.delete_many({})
    db.matches_clean.delete_many({})
    db.aggregated_stats.delete_many({})
    return {"message": "Database completely wiped. Ready for fresh start."}


@app.get("/refresh")
def force_refresh():
    """
    Triggers a manual refresh for ALL tracked users.
    Distributes tasks to Redis in batches of 50 to allow multiple extractors
    to process them in parallel without hitting rate limits.
    """
    try:
        users = list(db.summoners.find({}, {"puuid": 1, "summonerName": 1}))

        if not users:
            return {"status": "No users to refresh"}

        count = 0
        BATCH_SIZE = 50
        LIMIT_TO_REFRESH = 100

        for u in users:
            for start in range(0, LIMIT_TO_REFRESH, BATCH_SIZE):
                redis_client.lpush("extraction_queue", json.dumps({
                    "action": "extract_batch",
                    "puuid": u["puuid"],
                    "start": start,
                    "count": BATCH_SIZE,
                    "update_profile": (start == 0)
                }))
                count += 1

        return {"status": f"Distributed {count} batch tasks to Queue"}
    except Exception as e:
        return {"status": "Error", "detail": str(e)}


@app.get("/stats/{summoner}")
def get_stats(summoner: str):
    """
    Returns the aggregated dashboard data for a specific summoner.
    Includes Profile, Rank, Recent Matches, and Champion Stats.
    """
    if not check_db(): raise HTTPException(503, "DB Down")

    clean_search = summoner.replace(" ", "")
    if "#" in summoner:
        parts = summoner.split("#")
        clean_search = f"{parts[0].strip()}#{parts[1].strip()}"

    query = {"summonerName": {"$regex": f"^{re.escape(clean_search)}$", "$options": "i"}}

    summ = db.summoners.find_one({"summonerName": summoner})
    if not summ:
        summ = db.summoners.find_one(query)

    if not summ: return {"error": "not found"}

    puuid = summ.get("puuid")

    matches = list(
        db.matches_clean.find({"puuid": puuid}, {"_id": 0})
        .sort([("game_timestamp", -1)])
        .limit(300)
    )

    agg_dict = {}
    for m in matches:
        champ = m.get("champion", "Unknown")
        if champ not in agg_dict: agg_dict[champ] = {"games": 0, "wins": 0, "k": 0, "d": 0, "a": 0}
        s = agg_dict[champ]
        s["games"] += 1
        if m.get("win"): s["wins"] += 1
        s["k"] += m.get("kills", 0)
        s["d"] += m.get("deaths", 0)
        s["a"] += m.get("assists", 0)

    aggregated = []
    for champ, s in agg_dict.items():
        kda = (s["k"] + s["a"]) / max(1, s["d"])
        winrate = (s["wins"] / s["games"]) * 100
        aggregated.append({
            "champion": champ, "games": s["games"], "wins": s["wins"],
            "winrate": round(winrate, 1), "avg_kda": round(kda, 2)
        })

    return {
        "summoner": summ.get("summonerName"),
        "profile_icon": summ.get("profileIconId", 29),
        "level": summ.get("summonerLevel", 0),
        "rank_solo": {
            "tier": summ.get("solo_tier", "UNRANKED"),
            "rank": summ.get("solo_rank", ""),
            "lp": summ.get("solo_lp", 0),
            "wins": summ.get("solo_wins", 0),
            "losses": summ.get("solo_losses", 0)
        },
        "matches": matches,
        "aggregated": aggregated
    }