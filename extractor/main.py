from fastapi import FastAPI, BackgroundTasks
import requests
from pymongo import MongoClient
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import sys

load_dotenv()

RIOT_API_KEY = os.getenv("RIOT_API_KEY")

mongo = MongoClient("mongodb://db:27017")
db = mongo["riot"]


def log(msg):
    print(msg)
    sys.stdout.flush()


def riot_get(url, timeout=10):
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 429: time.sleep(120)
        if r.status_code == 200: return r.json()
    except:
        pass
    return None


def get_region_and_platform(name_tag):
    if "#" in name_tag:
        tag = name_tag.split("#")[-1].upper()
    else:
        tag = "EUW"

    if tag == "KR1": return "kr", "asia"
    if tag == "NA1": return "na1", "americas"

    return "euw1", "europe"


def update_db_rank_data(puuid, solo_data):
    """Guarda les dades de rang a la BD amb la data d'actualització"""
    if not solo_data:
        solo_data = {}

    # ÚS DE .get() PER A PL/GOLD/SILVER
    rank_data = {
        "last_rank_update": datetime.utcnow(),
        "solo_tier": solo_data.get('tier', "UNRANKED"),
        "solo_rank": solo_data.get('rank', ""),  # Utilitzem .get() per defecte
        "solo_lp": solo_data.get('leaguePoints', 0),
        "solo_wins": solo_data.get('wins', 0),
        "solo_losses": solo_data.get('losses', 0),
    }
    db.summoners.update_one({"puuid": puuid}, {"$set": rank_data})

    # ÚS DE .get() AL MISSATGE DE LOG PER NO PETAR
    if solo_data.get('tier'):
        # Fix del KeyError: 'rank'
        rank_display = solo_data.get('rank', 'I') if solo_data.get('tier') in ["DIAMOND", "EMERALD", "PLATINUM", "GOLD",
                                                                               "SILVER", "BRONZE", "IRON"] else ""
        log(f"🏅 RANG OK: {solo_data['tier']} {rank_display}")
    return True


def fetch_and_update_rank_fast(enc_id, platform, puuid, name):
    """Intent 1: Consulta directa League-V4 (només si tenim ID)"""
    try:
        league_url = f"https://{platform}.api.riotgames.com/lol/league/v4/entries/by-summoner/{enc_id}?api_key={RIOT_API_KEY}"
        data = riot_get(league_url)

        if data:
            solo = next((l for l in data if l['queueType'] == 'RANKED_SOLO_5x5'), None)

            # Si trobem SoloQ O ens torna la llista buida (Unranked), actualitzem i sortim
            if solo or not data:
                return update_db_rank_data(puuid, solo)

    except Exception as e:
        log(f"🔥 Error consultant lliga {name}: {e}")
        return False
    return False


def fetch_rank_advanced(puuid, platform, name):
    """Intent 2 (PLA C): Mètode League-Exp-V4 per jugadors d'alt Elo"""
    queues = ["RANKED_SOLO_5x5"]
    tiers_high_elo = ["CHALLENGER", "GRANDMASTER", "MASTER"]  # Només Master/GM/Challenger

    log(f"🔍 [EXP-V4] Iniciant cerca ALT ELO per {name}...")

    # Busquem ELO alt
    for tier in tiers_high_elo:
        # Construïm la URL específica per a la plataforma
        url = f"https://{platform}.api.riotgames.com/lol/league/v4/{tier.lower()}leagues/by-queue/RANKED_SOLO_5x5?api_key={RIOT_API_KEY}"

        # Casos especials de Challenger/GM/Master
        if tier == "CHALLENGER":
            url = f"https://{platform}.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5?api_key={RIOT_API_KEY}"
        elif tier == "GRANDMASTER":
            url = f"https://{platform}.api.api.riotgames.com/lol/league/v4/grandmasterleagues/by-queue/RANKED_SOLO_5x5?api_key={RIOT_API_KEY}"
        elif tier == "MASTER":
            url = f"https://{platform}.api.riotgames.com/lol/league/v4/masterleagues/by-queue/RANKED_SOLO_5x5?api_key={RIOT_API_KEY}"

        data = riot_get(url)
        if data and 'entries' in data:
            for entry in data['entries']:
                if entry.get("puuid") == puuid:
                    # RANG TROBAT!
                    update_data = {
                        'tier': tier,
                        'rank': entry.get('rank', 'I') if tier != 'MASTER' else 'I',
                        # Master no té divisió, però la BD la necessita
                        'leaguePoints': entry.get('leaguePoints', 0),
                        'wins': entry.get('wins', 0),
                        'losses': entry.get('losses', 0)
                    }
                    update_db_rank_data(puuid, update_data)
                    log(f"🎉 RANG EXP-V4 OK: {name} -> {tier}")
                    return True

    # Si no es troba en ALT ELO, marquem com UNRANKED (segons la teva petició)
    log(f"⚠️ RANG AVANÇAT: {name} no trobat a Master+ llistes (Assignant UNRANKED)")
    update_db_rank_data(puuid, {'tier': 'UNRANKED'})
    return True


def run_extraction_job(limit=50):
    log(f"⏰ [AUTO] Cicle iniciat ({limit} partides)")
    if not RIOT_API_KEY: return

    try:
        raw_summoners = list(db.summoners.find({}))
    except:
        return

    unique_summoners = {s['puuid']: s for s in raw_summoners}.values()

    for summ in unique_summoners:
        puuid = summ.get("puuid")
        name = summ.get("summonerName")
        saved_id = summ.get("encryptedSummonerId")
        platform, region = get_region_and_platform(name)

        # 1. ACTUALITZAR RANG
        rank_updated = False
        if saved_id:
            rank_updated = fetch_and_update_rank_fast(saved_id, platform, puuid, name)

        if not rank_updated:
            rank_updated = fetch_rank_advanced(puuid, platform, name)  # Només busca Challenger/GM/Master

        # 2. BAIXAR PARTIDES (El mateix que abans)
        ids_url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?count={limit}&api_key={RIOT_API_KEY}"
        try:
            r = requests.get(ids_url, timeout=10)
            if r.status_code == 429: time.sleep(120); continue

            if r.status_code == 200:
                match_ids = r.json()
                new_c = 0
                for match_id in match_ids:
                    exists = db.matches_raw.find_one({"matchId": match_id})

                    if exists and saved_id: continue

                    m_url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={RIOT_API_KEY}"
                    raw_r = requests.get(m_url, timeout=10)

                    if raw_r.status_code == 429: time.sleep(120); continue

                    if raw_r.status_code == 200:
                        data = raw_r.json()

                        # RECUPERACIÓ D'ID D'EMERGÈNCIA (Si encara ens falta l'ID)
                        if not saved_id:
                            participants = data.get('info', {}).get('participants', [])
                            for p in participants:
                                if p.get('puuid') == puuid:
                                    eid = p.get('summonerId')
                                    if eid:
                                        log(f"🔍 ID Trobat a partida! Actualitzant BD i consultant rang...")
                                        db.summoners.update_one({"puuid": puuid},
                                                                {"$set": {"encryptedSummonerId": eid}})
                                        saved_id = eid
                                        fetch_and_update_rank_fast(eid, platform, puuid, name)
                                    break

                        if not exists:
                            db.matches_raw.insert_one(
                                {"matchId": match_id, "puuid": puuid, "raw": data, "processed": False,
                                 "timestamp": datetime.utcnow()})
                            new_c += 1

                        time.sleep(0.1)

                if new_c > 0: log(f"📥 {name}: +{new_c} partides.")

        except Exception as e:
            log(f"❌ Error Partides {name}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_extraction_job, 'interval', minutes=30, kwargs={"limit": 50})
    scheduler.start()
    log("🚀 Extractor Final BINDAT INICIAT")
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root(): return {"status": "Running"}


@app.get("/trigger_extract")
def manual_trigger(background_tasks: BackgroundTasks, count: int = 50):
    background_tasks.add_task(run_extraction_job, limit=count)
    return {"status": "Job started"}