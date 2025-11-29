import requests
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("RIOT_API_KEY")

PLATFORM = "euw1"
REGION = "europe"

def riot_get(url):
    r = requests.get(url)
    if r.status_code != 200:
        return None
    return r.json()

def get_rank(game_name, tag_line):
    acc_url = (
        f"https://{REGION}.api.riotgames.com/riot/account/v1/accounts/"
        f"by-riot-id/{game_name}/{tag_line}?api_key={API_KEY}"
    )

    acc_data = riot_get(acc_url)
    if not acc_data:
        return {"error": "riot-id not found"}

    puuid = acc_data["puuid"]

    sum_url = (
        f"https://{PLATFORM}.api.riotgames.com/lol/summoner/v4/summoners/"
        f"by-puuid/{puuid}?api_key={API_KEY}"
    )
    sum_data = riot_get(sum_url)

    if sum_data and "id" in sum_data:
        encrypted_id = sum_data["id"]

        league_url = (
            f"https://{PLATFORM}.api.riotgames.com/lol/league/v4/entries/"
            f"by-summoner/{encrypted_id}?api_key={API_KEY}"
        )
        leagues = riot_get(league_url)
        return {"method": "summoner_v4", "rank": leagues, "puuid": puuid}

    print("⚠ Summoner-V4 failed, using league-exp-v4...")

    queues = ["RANKED_SOLO_5x5", "RANKED_FLEX_SR"]
    tiers = ["CHALLENGER", "GRANDMASTER","MASTER",
             "DIAMOND","EMERALD","PLATINUM","GOLD",
             "SILVER","BRONZE","IRON"]
    divs = ["I","II","III","IV"]

    for queue in queues:
        for tier in tiers:
            for div in divs:
                url = (
                    f"https://{PLATFORM}.api.riotgames.com/lol/league-exp/v4/"
                    f"entries/{queue}/{tier}/{div}?api_key={API_KEY}"
                )

                data = riot_get(url)
                if not data:
                    continue

                for entry in data:
                    if entry["puuid"] == puuid:
                        return {
                            "method": "league_exp_v4",
                            "rank": entry,
                            "puuid": puuid
                        }

    return {"error": "Rank not found", "puuid": puuid}

if __name__ == "__main__":
    result = get_rank("FerroiLlautó", "PUJOL")
    print(result)
