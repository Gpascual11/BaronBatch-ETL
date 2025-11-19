from fastapi import FastAPI
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager

load_dotenv()

mongo = MongoClient("mongodb://db:27017")
db = mongo["riot"]


# --- LÒGICA DE TRANSFORMACIÓ ---
def run_transform_job():
    # Busquem partides que NO tinguin processed: True
    raw_matches = list(db.matches_raw.find({"processed": False}))

    if not raw_matches:
        # No imprimim res per no omplir els logs de brossa si no hi ha feina
        return

    print(f"⚙️ [AUTO] Processant {len(raw_matches)} partides noves...")

    for raw in raw_matches:
        data = raw.get("raw")
        match_id = raw.get("matchId")
        puuid = raw.get("puuid")

        # Validacions bàsiques
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

        # Càlculs (ETL)
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

        # Guardem a Clean
        db.matches_clean.insert_one(clean_doc)

        # Marquem Raw com processada
        db.matches_raw.update_one({"_id": raw["_id"]}, {"$set": {"processed": True}})

        # Actualitzem Estadístiques Agregades
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
    print("✅ Processament automàtic finalitzat.")


# --- LIFESPAN (SCHEDULER) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    # Executa cada 2 minuts per ser ràpid quan arriben dades
    scheduler.add_job(run_transform_job, 'interval', minutes=2)
    scheduler.start()
    print("🚀 Scheduler del Transformer INICIAT (cada 2 minuts)")
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root():
    return {"status": "Transformer Running", "mode": "Automatic (Every 2m)"}


@app.get("/trigger_process")
def manual_trigger():
    run_transform_job()
    return {"status": "Manual job triggered"}