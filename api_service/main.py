from fastapi import FastAPI
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

mongo = MongoClient("mongodb://db:27017")
db = mongo["riot"]

app = FastAPI()

@app.get("/stats/{summoner}")
def get_stats(summoner: str):
    summ = db.summoners.find_one({"summonerName": summoner})
    if not summ:
        return {"error": "summoner not found"}

    puuid = summ.get("puuid")
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
