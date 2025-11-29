from fastapi import FastAPI
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import sys
import unicodedata

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

        # Robust name extraction handling None/Null values
        name = p.get("riotIdGameName") or p.get("summonerName") or "Unknown"
        tag = p.get("riotIdTagLine") or ""

        full_name = f"{name}#{tag}" if tag else name

        extended_list.append({
            "champion": p.get("championName"),
            "summonerName": full_name,
            "teamId": p.get("teamId"),
            "win": p.get("win"),
            "kills": p.get("kills", 0),
            "deaths": p.get("deaths", 0),
            "assists": p.get("assists", 0),
            "total_damage": p.get("totalDamageDealtToChampions", 0),
            "items": items
        })
    return extended_list


def norm(s):
    """Normalize string: remove accents, lowercase, strip."""
    return unicodedata.normalize('NFKC', s).lower().strip() if s else ""


def run_transform_job():
    # Only get unprocessed matches
    raw_matches = list(db.matches_raw.find({"processed": False}))
    if not raw_matches: return

    log(f"⚙️ [AUTO] Processing {len(raw_matches)} matches...")
    processed_count = 0

    for raw in raw_matches:
        data = raw.get("raw")
        match_id = raw.get("matchId")
        # This is the "Master" PUUID (Key #1)
        db_puuid = raw.get("puuid")

        if not data or "info" not in data:
            db.matches_raw.update_one({"_id": raw["_id"]}, {"$set": {"processed": True}})
            continue

        all_participants = data["info"].get("participants", [])

        # --- STEP 1: Try Direct PUUID Match (Works for Extractor 1) ---
        target_p = next((p for p in all_participants if p.get("puuid") == db_puuid), None)

        # --- STEP 2: Fallback (Works for Extractor 2 / Key Mismatch) ---
        full_name = "Unknown"
        if not target_p:
            # We need to find the user by Name#Tag because the PUUID in JSON is different
            summ_doc = db.summoners.find_one({"puuid": db_puuid})

            if summ_doc and "summonerName" in summ_doc:
                full_name = summ_doc["summonerName"]
                if "#" in full_name:
                    target_game_name = norm(full_name.split("#")[0])
                    target_tag_line = norm(full_name.split("#")[1])

                    # Strategy A: Riot ID Match (Strict OR Loose if tag is missing in game data)
                    target_p = next((
                        p for p in all_participants
                        if norm(p.get("riotIdGameName")) == target_game_name
                           and (
                                   norm(p.get("riotIdTagLine")) == target_tag_line or
                                   not p.get("riotIdTagLine")  # ACCEPT IF MATCH DATA HAS NO TAG
                           )
                    ), None)

                    # Strategy B: Fallback to Summoner Name (Common if RiotID is empty)
                    if not target_p:
                        target_p = next((
                            p for p in all_participants
                            if norm(p.get("summonerName")) == target_game_name
                        ), None)

        # If STILL not found, skip it
        if not target_p:
            # Enhanced Logging: Print available names to help debug why it failed
            try:
                available = [f"{p.get('riotIdGameName')}#{p.get('riotIdTagLine')}" for p in all_participants]
                log(f"⚠️ Could not find player {db_puuid} in match {match_id}. Skipping.")
                log(f"   Target: {full_name}")
                log(f"   Available in match: {available[:3]}...")  # Log first 3 to keep it clean
            except:
                pass

            db.matches_raw.update_one({"_id": raw["_id"]}, {"$set": {"processed": True}})
            continue

        # --- STANDARD EXTRACTION LOGIC ---
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
            "puuid": db_puuid,  # IMPORTANT: Use the DB PUUID, not the JSON PUUID
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
        processed_count += 1

        champ = target_p.get("championName")
        db.aggregated_stats.update_one(
            {"puuid": db_puuid, "champion": champ},
            {
                "$inc": {"games": 1, "wins": 1 if target_p.get("win") else 0, "kda_sum": clean_doc["kda"]}
            },
            upsert=True
        )

    log(f"✅ Transformation complete. Processed {processed_count} matches.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_transform_job, 'interval', minutes=1)
    scheduler.start()
    log("🚀 Transformer Started (Key-Mismatch Fix Enabled)")
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root(): return {"status": "Transformer Running"}


@app.get("/trigger_process")
def manual_trigger():
    run_transform_job()
    return {"status": "Manual job triggered"}