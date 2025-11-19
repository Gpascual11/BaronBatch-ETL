from fastapi import FastAPI
import requests
from pymongo import MongoClient
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager

load_dotenv()

REGION = "europe"
RIOT_API_KEY = os.getenv("RIOT_API_KEY")

mongo = MongoClient("mongodb://db:27017")
db = mongo["riot"]


# Acceptem un argument 'limit' per si volem canviar-ho manualment
def run_extraction_job(limit=50):
    print(f"⏰ [AUTO] Iniciant cicle d'extracció ({limit} partides): {datetime.utcnow()}")

    if not RIOT_API_KEY:
        print("❌ ERROR: No hi ha API Key configurada.")
        return

    raw_summoners = list(db.summoners.find({}))
    # Evitem duplicats
    unique_summoners = {s['puuid']: s for s in raw_summoners}.values()

    if not unique_summoners:
        print("⚠️ No hi ha jugadors per monitoritzar.")
        return

    for summ in unique_summoners:
        puuid = summ.get("puuid")
        name = summ.get("summonerName")

        # 1. IDs (Demanem 'limit' partides, ara 50)
        ids_url = f"https://{REGION}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?count={limit}&api_key={RIOT_API_KEY}"
        try:
            r = requests.get(ids_url, timeout=10)

            # GESTIÓ RATE LIMIT (429)
            if r.status_code == 429:
                print(f"⛔ Rate Limit (429) en IDs de {name}. Esperant 2 MINUTS...")
                time.sleep(120)  # <--- PAUSA DE 2 MINUTS (120s)
                continue

            r.raise_for_status()
            match_ids = r.json()

            time.sleep(0.5)

        except Exception as e:
            print(f"❌ Error baixant IDs de {name}: {e}")
            continue

        # 2. Detalls de la partida
        new_matches = 0
        for match_id in match_ids:
            # Si ja la tenim, saltem ràpid
            if db.matches_raw.find_one({"matchId": match_id}):
                continue

            match_url = f"https://{REGION}.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={RIOT_API_KEY}"
            try:
                raw_r = requests.get(match_url, timeout=10)

                if raw_r.status_code == 429:
                    print("⛔ Rate Limit (429) baixant partides. Pausa de 2 MINUTS...")
                    time.sleep(120)  # <--- PAUSA DE 2 MINUTS (120s)
                    # Després de descansar, tornem a intentar aquesta mateixa partida?
                    # Per simplificar el bucle, fem continue i la perdrem per aquest cicle,
                    # però la recuperarem en el següent cicle de 30min.
                    continue

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
                time.sleep(0.2)

            except Exception as e:
                print(f"⚠️ Error baixant partida {match_id}: {e}")

        if new_matches > 0:
            print(f"✅ {name}: Guardades {new_matches} partides noves.")
        else:
            print(f"zzz {name}: Tot al dia.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    # Programem el job passant limit=50
    scheduler.add_job(run_extraction_job, 'interval', minutes=30, kwargs={"limit": 50})
    scheduler.start()
    print("🚀 Scheduler de l'Extractor INICIAT (50 partides / 30 min)")
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root():
    return {"status": "Extractor Running", "config": "50 matches limit"}


# Trigger Manual permet sobreescriure el límit si vols
@app.get("/trigger_extract")
def manual_trigger(count: int = 50):
    run_extraction_job(limit=count)
    return {"status": "Manual job started check logs."}