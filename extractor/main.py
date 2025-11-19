from fastapi import FastAPI
import requests
from pymongo import MongoClient
import os
from datetime import datetime
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager

load_dotenv()

REGION = "europe"
RIOT_API_KEY = os.getenv("RIOT_API_KEY")

mongo = MongoClient("mongodb://db:27017")
db = mongo["riot"]


# --- LÒGICA D'EXTRACCIÓ (FUNCIO PURA) ---
def run_extraction_job():
    print(f"⏰ [AUTO] Iniciant cicle d'extracció: {datetime.utcnow()}")

    if not RIOT_API_KEY:
        print("❌ ERROR: No hi ha API Key configurada.")
        return

    summoners = list(db.summoners.find({}))
    if not summoners:
        print("⚠️ No hi ha jugadors per monitoritzar.")
        return

    count = 5  # Nombre de partides a mirar per defecte en automàtic

    for summ in summoners:
        puuid = summ.get("puuid")
        name = summ.get("summonerName")

        # 1. IDs
        ids_url = f"https://{REGION}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?count={count}&api_key={RIOT_API_KEY}"
        try:
            r = requests.get(ids_url, timeout=10)
            r.raise_for_status()
            match_ids = r.json()
        except Exception as e:
            print(f"❌ Error baixant IDs de {name}: {e}")
            continue

        # 2. Detalls
        new_matches = 0
        for match_id in match_ids:
            # Si ja la tenim, saltem
            if db.matches_raw.find_one({"matchId": match_id}):
                continue

            match_url = f"https://{REGION}.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={RIOT_API_KEY}"
            try:
                raw_r = requests.get(match_url, timeout=10)
                raw_r.raise_for_status()
                raw = raw_r.json()

                db.matches_raw.insert_one({
                    "matchId": match_id,
                    "puuid": puuid,
                    "raw": raw,
                    "processed": False,
                    "timestamp": datetime.utcnow()
                })
                new_matches += 1
            except Exception as e:
                print(f"⚠️ Error baixant partida {match_id}: {e}")

        if new_matches > 0:
            print(f"✅ {name}: Guardades {new_matches} partides noves.")
        else:
            print(f"zzz {name}: Cap partida nova trobada.")


# --- LIFESPAN (ARRANCAR SCHEDULER AL INICI) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Codi que s'executa quan s'engega el contenidor
    scheduler = BackgroundScheduler()
    # Executa la feina cada 30 minuts
    scheduler.add_job(run_extraction_job, 'interval', minutes=30)
    scheduler.start()
    print("🚀 Scheduler de l'Extractor INICIAT (cada 30 minuts)")

    yield  # L'app corre aquí

    # Codi quan es tanca (opcional)
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root():
    return {"status": "Extractor Running", "mode": "Automatic (Every 30m)"}


# Trigger Manual (per si vols forçar-ho)
@app.get("/trigger_extract")
def manual_trigger(count: int = 5):
    run_extraction_job()  # Crida la mateixa lògica
    return {"status": "Manual job finished. Check docker logs for details."}