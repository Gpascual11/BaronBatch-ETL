from fastapi import FastAPI
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

app = FastAPI()

mongo = MongoClient("mongodb://db:27017")
db = mongo["riot"]

@app.get("/trigger_process")
def process_raw_matches():
    raw_matches = list(db.matches_raw.find({"processed": False}))

    if not raw_matches:
        return {"status": "no raw matches to process"}

    for raw in raw_matches:
        data = raw.get("raw")
        match_id = raw.get("matchId")
        puuid = raw.get("puuid")

        if not data or "info" not in data:
            db.matches_raw.update_one({"_id": raw["_id"]}, {"$set": {"processed": True}})
            continue

        participant = None
        for p in data["info"].get("participants", []):
            if p.get("puuid") == puuid:
                participant = p
                break

        if not participant:
            db.matches_raw.update_one({"_id": raw["_id"]}, {"$set": {"processed": True}})
            continue

        deaths = participant.get("deaths", 0)
        kda = (participant.get("kills", 0) + participant.get("assists", 0)) / max(1, deaths)
        duration = data["info"].get("gameDuration", 1)
        cs = participant.get("totalMinionsKilled", 0) + participant.get("neutralMinionsKilled", 0)
        cs_min = cs / (duration / 60) if duration > 0 else 0.0

        clean_doc = {
            "matchId": match_id,
            "puuid": puuid,
            "champion": participant.get("championName"),
            "win": participant.get("win"),
            "kills": participant.get("kills"),
            "deaths": participant.get("deaths"),
            "assists": participant.get("assists"),
            "kda": round(kda, 2),
            "cs": cs,
            "cs_min": round(cs_min, 2),
            "timestamp": datetime.utcnow()
        }

        db.matches_clean.insert_one(clean_doc)
        db.matches_raw.update_one({"_id": raw["_id"]}, {"$set": {"processed": True}})

        # Update aggregated stats
        champ = participant.get("championName")
        stats = db.aggregated_stats.find_one({"puuid": puuid, "champion": champ})

        if not stats:
            db.aggregated_stats.insert_one({
                "puuid": puuid,
                "champion": champ,
                "games": 1,
                "wins": 1 if participant.get("win") else 0,
                "kda_sum": clean_doc["kda"]
            })
        else:
            db.aggregated_stats.update_one(
                {"puuid": puuid, "champion": champ},
                {
                    "$inc": {"games": 1, "wins": 1 if participant.get("win") else 0, "kda_sum": clean_doc["kda"]}
                }
            )

    return {"status": "ok", "processed": len(raw_matches)}
