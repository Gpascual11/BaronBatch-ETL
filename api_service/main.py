from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient
import os
import requests
from dotenv import load_dotenv

load_dotenv()

RIOT_API_KEY = os.getenv("RIOT_API_KEY")
REGION = "europe"  # O la que vulguis per defecte

mongo = MongoClient("mongodb://db:27017")
db = mongo["riot"]

app = FastAPI()


# Model per rebre dades des del dashboard
class SummonerRequest(BaseModel):
    name_tag: str  # Format: Nom#Tag


@app.get("/summoners")
def get_summoners_list():
    """Retorna la llista de tots els jugadors monitoritzats"""
    # Només retornem el nom per fer la llista lleugera
    summoners = list(db.summoners.find({}, {"summonerName": 1, "_id": 0}))
    return [s["summonerName"] for s in summoners]


@app.post("/add_summoner")
def add_summoner(request: SummonerRequest):
    full_name = request.name_tag

    if "#" not in full_name:
        raise HTTPException(status_code=400, detail="Format incorrecte. Usa: Nom#Tag")

    game_name, tag_line = full_name.split("#", 1)

    # 1. Comprovem si ja existeix a la BD
    existing = db.summoners.find_one({"summonerName": full_name})
    if existing:
        return {"message": f"El jugador {full_name} ja està monitoritzat."}

    # 2. Preguntem a Riot el PUUID (Account V1)
    url = f"https://{REGION}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}?api_key={RIOT_API_KEY}"

    r = requests.get(url)
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Jugador no trobat a Riot Games.")
    elif r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Error API Riot: {r.status_code}")

    data = r.json()
    puuid = data.get("puuid")
    real_name = f"{data.get('gameName')}#{data.get('tagLine')}"

    # 3. Guardem a Mongo
    db.summoners.insert_one({
        "summonerName": real_name,
        "puuid": puuid,
        "region": REGION
    })

    # 4. MÀGIA: Despertem l'Extractor automàticament!
    # Fem una crida interna al contenidor 'extractor'
    try:
        requests.get("http://extractor:8000/trigger_extract?count=5", timeout=5)
    except:
        pass  # Si falla el trigger no passa res, el scheduler ho farà en 30 min

    return {"message": f"✅ Jugador {real_name} afegit correctament! Baixant dades..."}


@app.get("/stats/{summoner}")
def get_stats(summoner: str):
    # Endpoint de lectura (igual que abans)
    summ = db.summoners.find_one({"summonerName": summoner})

    if not summ:
        return {"error": "summoner not found"}

    puuid = summ.get("puuid")
    matches = list(db.matches_clean.find({"puuid": puuid}, {"_id": 0}).sort("timestamp", -1).limit(20))
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