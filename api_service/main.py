from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient
import os
import requests
from dotenv import load_dotenv

load_dotenv()

RIOT_API_KEY = os.getenv("RIOT_API_KEY")
REGION = "europe"

mongo = MongoClient("mongodb://db:27017")
db = mongo["riot"]

app = FastAPI()


class SummonerRequest(BaseModel):
    name_tag: str


@app.get("/summoners")
def get_summoners_list():
    summoners = list(db.summoners.find({}, {"summonerName": 1, "_id": 0}))
    return sorted(list(set([s["summonerName"] for s in summoners])))


@app.post("/add_summoner")
def add_summoner(request: SummonerRequest):
    full_name = request.name_tag

    if "#" not in full_name:
        raise HTTPException(status_code=400, detail="Format incorrecte. Usa: Nom#Tag")

    game_name, tag_line = full_name.split("#", 1)

    # 1. API RIOT (Validació)
    url = f"https://{REGION}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}?api_key={RIOT_API_KEY}"

    r = requests.get(url)
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Jugador no trobat.")
    elif r.status_code == 429:
        raise HTTPException(status_code=429, detail="Rate Limit (429). L'API està descansant.")
    elif r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Error API Riot: {r.status_code}")

    data = r.json()
    puuid = data.get("puuid")
    real_name = f"{data.get('gameName')}#{data.get('tagLine')}"

    # 2. Comprovació duplicats
    existing = db.summoners.find_one({"puuid": puuid})
    if existing:
        db.summoners.update_one({"puuid": puuid}, {"$set": {"summonerName": real_name}})
        return {"message": f"El jugador {real_name} ja estava monitoritzat."}

    # 3. Insertar
    db.summoners.insert_one({
        "summonerName": real_name,
        "puuid": puuid,
        "region": REGION
    })

    # 4. Trigger Extractor (ARA AMB COUNT=50)
    try:
        # Timeout molt curt perquè és 'fire and forget', l'extractor trigarà una estona en baixar 50 partides
        requests.get("http://extractor:8000/trigger_extract?count=50", timeout=1)
    except:
        pass

    return {"message": f"✅ Jugador {real_name} afegit! Baixant les 50 últimes partides..."}


@app.get("/stats/{summoner}")
def get_stats(summoner: str):
    summ = db.summoners.find_one({"summonerName": summoner})

    if not summ:
        return {"error": "summoner not found"}

    puuid = summ.get("puuid")
    # Augmentem també el límit de lectura a 50 per veure-les totes al dashboard
    matches = list(db.matches_clean.find({"puuid": puuid}, {"_id": 0}).sort("timestamp", -1).limit(50))
    aggregated_raw = list(db.aggregated_stats.find({"puuid": puuid}, {"_id": 0}))

    aggregated = []
    for a in aggregated_raw:
        games = a.get("games", 0)
        wins = a.get("wins", 0)
        kda_avg = a.get("kda_sum", 0) / max(1, games)
        aggregated.append({
            "champion": a.get("champion"),
            "games": games,
            "wins": wins,
            "winrate": round(100 * wins / max(1, games), 2),
            "avg_kda": round(kda_avg, 2)
        })

    return {
        "summoner": summ.get("summonerName"),
        "matches": matches,
        "aggregated": aggregated
    }