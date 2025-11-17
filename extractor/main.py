from fastapi import FastAPI, HTTPException
import requests
from pymongo import MongoClient
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

RIOT_API_KEY = os.getenv("RIOT_API_KEY")
if not RIOT_API_KEY:
    raise RuntimeError("RIOT_API_KEY not found in environment")

MONGO_URL = "mongodb://db:27017"
mongo = MongoClient(MONGO_URL)
db = mongo["riot"]

REGION = "europe"

@app.get("/trigger_extract")
def extract_matches(count: int = 10):
    summoners = list(db.summoners.find({}))

    if not summoners:
        return {"status": "no summoners to scan"}

    for summ in summoners:
        puuid = summ.get("puuid")
        if not puuid:
            continue

        ids_url = "https://{region}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?count={count}&api_key={key}".format(
            region=REGION, puuid=puuid, count=count, key=RIOT_API_KEY
        )
        try:
            r = requests.get(ids_url, timeout=10)
            r.raise_for_status()
            match_ids = r.json()
        except Exception as e:
            # skip this summoner if api fails
            continue

        for match_id in match_ids:
            # avoid duplicates
            if db.matches_raw.find_one({"matchId": match_id}):
                continue

            match_url = "https://{region}.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={key}".format(
                region=REGION, match_id=match_id, key=RIOT_API_KEY
            )
            try:
                raw_r = requests.get(match_url, timeout=10)
                raw_r.raise_for_status()
                raw = raw_r.json()
            except Exception as e:
                continue

            db.matches_raw.insert_one({
                "matchId": match_id,
                "puuid": puuid,
                "raw": raw,
                "processed": False,
                "timestamp": datetime.utcnow()
            })

    return {"status": "ok", "message": "Extraction done"}
