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

# --- REDIS CONNECTION ---
redis_client = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)

app = FastAPI()


class SummonerRequest(BaseModel):
    name_tag: str


def get_routing_info(tag):
    tag = tag.upper()
    if tag == "KR1": return "asia", "kr"
    if tag == "NA1": return "americas", "na1"
    return "europe", "euw1"


def check_db():
    try:
        mongo.admin.command('ping')
        return True
    except:
        return False


@app.get("/summoners")
def get_summoners_list():
    if not check_db(): return []
    try:
        summoners = list(db.summoners.find({}, {"summonerName": 1, "_id": 0}))
        return sorted(list(set([s["summonerName"] for s in summoners])))
    except:
        return []


@app.post("/add_summoner")
def add_summoner(request: SummonerRequest):
    if not check_db(): raise HTTPException(503, "DB Loading...")

    full_name = request.name_tag
    if "#" not in full_name: raise HTTPException(400, "Format: Name#Tag")

    tag = full_name.split("#")[-1].strip()
    game_name = full_name.split("#")[0].strip()

    api_region, platform = get_routing_info(tag)

    acc_url = f"https://{api_region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag}?api_key={RIOT_API_KEY}"

    try:
        r = requests.get(acc_url, timeout=5)
    except:
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

    # Push single task to Redis
    try:
        task_payload = {
            "puuid": puuid,
            "limit": 200,
            "action": "extract"
        }
        redis_client.lpush("extraction_queue", json.dumps(task_payload))
    except Exception as e:
        print(f"⚠️ Redis Error: {e}")

    return {
        "message": f"✅ {real_name} queued for update (200 games)!",
        "correct_name": real_name
    }


@app.delete("/summoner/{name_tag}")
def delete_summoner(name_tag: str):
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

    return {"message": f"🗑️ Deleted {name} and all data."}


@app.delete("/maintenance/cleanup")
def cleanup_data():
    if not check_db(): raise HTTPException(503, "DB Down")

    valid_puuids = [s["puuid"] for s in db.summoners.find({}, {"puuid": 1})]
    raw_res = db.matches_raw.delete_many({"puuid": {"$nin": valid_puuids}})
    clean_res = db.matches_clean.delete_many({"puuid": {"$nin": valid_puuids}})

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
    except:
        pass

    return {
        "message": "Deep Clean Successful",
        "deleted_orphans": raw_res.deleted_count,
        "deleted_duplicates": deleted_dupes,
        "trimmed_excess": deleted_excess
    }


@app.delete("/maintenance/nuke")
def nuke_database():
    if not check_db(): raise HTTPException(503, "DB Down")
    db.summoners.delete_many({})
    db.matches_raw.delete_many({})
    db.matches_clean.delete_many({})
    db.aggregated_stats.delete_many({})
    return {"message": "💥 Database completely wiped. Ready for fresh start."}


@app.get("/refresh")
def force_refresh():
    """
    SPLIT THE JOB: Gets all users and pushes individual tasks to Redis.
    This allows multiple extractors to pick them up simultaneously.
    """
    try:
        # 1. Get all user PUUIDs
        users = list(db.summoners.find({}, {"puuid": 1, "summonerName": 1}))

        if not users:
            return {"status": "No users to refresh"}

        count = 0
        # 2. Create a separate task for each user
        for u in users:
            payload = {
                "action": "extract",
                "puuid": u["puuid"],
                "limit": 200  # Request 200 games per user
            }
            redis_client.lpush("extraction_queue", json.dumps(payload))
            count += 1

        return {"status": f"🚀 Distributed {count} tasks to Queue (Limit 200)"}

    except Exception as e:
        return {"status": "Error", "detail": str(e)}


@app.get("/stats/{summoner}")
def get_stats(summoner: str):
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
        .limit(200)
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