# seed_player.py
from pymongo import MongoClient
import requests

# CONFIGURACIÓ
RIOT_API_KEY = "RGAPI-182185eb-7df6-45b2-b842-7c29a4683fbe"  # <-- POSA LA CLAU
GAME_NAME = "Agurin" # <-- Nom del jugador
TAG_LINE = "DND"           # <-- Tag del jugador
REGION = "europe"          # O 'asia', 'americas' segons on sigui el compte (Hide on bush és KR = asia)

mongo = MongoClient("mongodb://localhost:27017")
db = mongo["riot"]

def get_puuid():
    # Compte: Riot va canviar a Account-V1 (Riot ID)
    url = f"https://{REGION}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{GAME_NAME}/{TAG_LINE}?api_key={RIOT_API_KEY}"
    res = requests.get(url)
    if res.status_code != 200:
        print(f"Error obtenint PUUID: {res.status_code} - {res.text}")
        return None
    return res.json().get("puuid")

puuid = get_puuid()

if puuid:
    # Inserim a la base de dades per a que l'extractor el trobi
    existing = db.summoners.find_one({"puuid": puuid})
    if not existing:
        db.summoners.insert_one({
            "summonerName": f"{GAME_NAME}#{TAG_LINE}",
            "puuid": puuid,
            "region": REGION
        })
        print(f"✅ Jugador {GAME_NAME}#{TAG_LINE} afegit a MongoDB!")
    else:
        print("El jugador ja existeix.")