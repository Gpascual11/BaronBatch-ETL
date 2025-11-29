# seed_player.py
from pymongo import MongoClient
import requests
import os

RIOT_API_KEY = os.getenv('RIOT_API_KEY')
GAME_NAME = "Agurin"
TAG_LINE = "DND"
REGION = "europe"

mongo = MongoClient("mongodb://localhost:27017")
db = mongo["riot"]

def get_puuid():
    url = f"https://{REGION}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{GAME_NAME}/{TAG_LINE}?api_key={RIOT_API_KEY}"
    res = requests.get(url)
    if res.status_code != 200:
        print(f"Error obtenint PUUID: {res.status_code} - {res.text}")
        return None
    return res.json().get("puuid")

puuid = get_puuid()

if puuid:
    existing = db.summoners.find_one({"puuid": puuid})
    if not existing:
        db.summoners.insert_one({
            "summonerName": f"{GAME_NAME}#{TAG_LINE}",
            "puuid": puuid,
            "region": REGION
        })
        print(f"Jugador {GAME_NAME}#{TAG_LINE} afegit a MongoDB!")
    else:
        print("El jugador ja existeix.")