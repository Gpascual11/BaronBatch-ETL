from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient
import os
import requests
from dotenv import load_dotenv

load_dotenv()

RIOT_API_KEY = os.getenv("RIOT_API_KEY")
mongo = MongoClient("mongodb://db:27017", serverSelectionTimeoutMS=3000)
db = mongo["riot"]

app = FastAPI()


class SummonerRequest(BaseModel):
    name_tag: str


def get_routing_info(tag):
    tag = tag.upper()
    if tag == "KR1": return "asia", "kr"
    if tag == "NA1": return "americas", "na1"
    return "europe", "euw1"


def check_db():
    try:
        mongo.admin.command('ping')
        return True
    except:
        return False


@app.get("/summoners")
def get_summoners_list():
    if not check_db(): return []
    try:
        summoners = list(db.summoners.find({}, {"summonerName": 1, "_id": 0}))
        return sorted(list(set([s["summonerName"] for s in summoners])))
    except:
        return []


@app.post("/add_summoner")
def add_summoner(request: SummonerRequest):
    if not check_db(): raise HTTPException(503, "DB Loading...")

    full_name = request.name_tag
    if "#" not in full_name: raise HTTPException(400, "Format: Name#Tag")

    tag = full_name.split("#")[-1]
    api_region, platform = get_routing_info(tag)
    game_name, tag_line = full_name.split("#", 1)

    acc_url = f"https://{api_region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}?api_key={RIOT_API_KEY}"

    try:
        r = requests.get(acc_url, timeout=5)
    except:
        raise HTTPException(504, "Timeout Riot API")

    if r.status_code == 404: raise HTTPException(404, "Player not found")
    if r.status_code != 200: raise HTTPException(400, "Error API")

    data = r.json()
    puuid = data.get("puuid")
    real_name = f"{data.get('gameName')}#{data.get('tagLine')}"

    # Insert initial data
    update_data = {
        "summonerName": real_name,
        "region": api_region,
    }

    db.summoners.update_one({"puuid": puuid}, {"$set": update_data}, upsert=True)

    # Trigger Extractor
    try:
        requests.get("http://extractor:8000/trigger_extract?count=50", timeout=0.5)
    except:
        pass

    return {"message": f"✅ {real_name} added! Fetching data..."}


# --- NEW ENDPOINT TO FIX DATA ---
@app.get("/refresh")
def force_refresh():
    """Manually triggers the Extractor to update Icons, Levels, and Matches"""
    try:
        # This calls the Extractor service internally
        requests.get("http://extractor:8000/trigger_extract?count=50", timeout=1)
        return {"status": "Update Signal Sent"}
    except Exception as e:
        return {"status": "Error", "detail": str(e)}


@app.get("/stats/{summoner}")
def get_stats(summoner: str):
    if not check_db(): raise HTTPException(503, "DB Down")

    summ = db.summoners.find_one({"summonerName": summoner})
    if not summ: return {"error": "not found"}

    puuid = summ.get("puuid")

    matches = list(
        db.matches_clean.find({"puuid": puuid}, {"_id": 0})
        .sort([("game_timestamp", -1)])
        .limit(50)
    )

    agg_dict = {}
    for m in matches:
        champ = m.get("champion", "Unknown")
        if champ not in agg_dict: agg_dict[champ] = {"games": 0, "wins": 0, "k": 0, "d": 0, "a": 0}
        s = agg_dict[champ]
        s["games"] += 1
        if m.get("win"): s["wins"] += 1
        s["k"] += m.get("kills", 0)
        s["d"] += m.get("deaths", 0)
        s["a"] += m.get("assists", 0)

    aggregated = []
    for champ, s in agg_dict.items():
        kda = (s["k"] + s["a"]) / max(1, s["d"])
        winrate = (s["wins"] / s["games"]) * 100
        aggregated.append({
            "champion": champ, "games": s["games"], "wins": s["wins"],
            "winrate": round(winrate, 1), "avg_kda": round(kda, 2)
        })

    return {
        "summoner": summ.get("summonerName"),
        "profile_icon": summ.get("profileIconId", 29),
        "level": summ.get("summonerLevel", 0),
        "rank_solo": {
            "tier": summ.get("solo_tier", "UNRANKED"),
            "rank": summ.get("solo_rank", ""),
            "lp": summ.get("solo_lp", 0),
            "wins": summ.get("solo_wins", 0),
            "losses": summ.get("solo_losses", 0)
        },
        "matches": matches,
        "aggregated": aggregated
    }