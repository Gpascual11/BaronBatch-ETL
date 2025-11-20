from fastapi import FastAPI
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import sys

load_dotenv()

mongo = MongoClient("mongodb://db:27017")
db = mongo["riot"]


def log(msg):
    print(msg)
    sys.stdout.flush()


def get_participants_extended(participants):
    """Extract detailed info (Items, KDA) for all 10 players"""
    extended_list = []
    for p in participants:
        items = [p.get(f"item{i}", 0) for i in range(7)]
        extended_list.append({
            "champion": p.get("championName"),
            "summonerName": p.get("riotIdGameName", p.get("summonerName")),
            "teamId": p.get("teamId"),
            "win": p.get("win"),
            "kills": p.get("kills", 0),
            "deaths": p.get("deaths", 0),
            "assists": p.get("assists", 0),
            "total_damage": p.get("totalDamageDealtToChampions", 0),
            "items": items
        })
    return extended_list


def run_transform_job():
    raw_matches = list(db.matches_raw.find({"processed": False}))
    if not raw_matches: return

    log(f"⚙️ [AUTO] Processing {len(raw_matches)} matches...")

    for raw in raw_matches:
        data = raw.get("raw")
        match_id = raw.get("matchId")
        puuid = raw.get("puuid")

        if not data or "info" not in data:
            db.matches_raw.update_one({"_id": raw["_id"]}, {"$set": {"processed": True}})
            continue

        all_participants = data["info"].get("participants", [])

        target_p = next((p for p in all_participants if p.get("puuid") == puuid), None)

        if not target_p:
            db.matches_raw.update_one({"_id": raw["_id"]}, {"$set": {"processed": True}})
            continue

        queue_id = data["info"].get("queueId", 0)
        game_ts_ms = data["info"].get("gameEndTimestamp", data["info"].get("gameCreation"))
        duration = data["info"].get("gameDuration", 1)

        deaths = target_p.get("deaths", 0)
        kda = (target_p.get("kills", 0) + target_p.get("assists", 0)) / max(1, deaths)

        cs = target_p.get("totalMinionsKilled", 0) + target_p.get("neutralMinionsKilled", 0)
        cs_min = cs / (duration / 60) if duration > 0 else 0.0
        items = [target_p.get(f"item{i}", 0) for i in range(7)]

        clean_doc = {
            "matchId": match_id,
            "puuid": puuid,
            "queue_id": queue_id,
            "champion": target_p.get("championName"),
            "win": target_p.get("win"),
            "kills": target_p.get("kills"),
            "deaths": deaths,
            "assists": target_p.get("assists"),
            "kda": round(kda, 2),
            "cs": cs,
            "cs_min": round(cs_min, 1),
            "total_damage": target_p.get("totalDamageDealtToChampions", 0),
            "gold_earned": target_p.get("goldEarned", 0),
            "items": items,
            "game_timestamp": game_ts_ms,
            "participants": get_participants_extended(all_participants),
            "processed_at": datetime.utcnow()
        }

        db.matches_clean.insert_one(clean_doc)
        db.matches_raw.update_one({"_id": raw["_id"]}, {"$set": {"processed": True}})

        champ = target_p.get("championName")
        db.aggregated_stats.update_one(
            {"puuid": puuid, "champion": champ},
            {
                "$inc": {"games": 1, "wins": 1 if target_p.get("win") else 0, "kda_sum": clean_doc["kda"]}
            },
            upsert=True
        )

    log("✅ Transformation complete.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_transform_job, 'interval', minutes=2)
    scheduler.start()
    log("🚀 Transformer Started")
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root(): return {"status": "Transformer Running"}


# --- RESTORED THIS ENDPOINT ---
@app.get("/trigger_process")
def manual_trigger():
    run_transform_job()
    return {"status": "Manual job triggered"}