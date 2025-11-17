"""Utility script to fetch PUUID for a summoner name and seed into MongoDB.
Run this locally (not inside container) with Python installed and access to Docker network,
or adapt it to run inside a tiny container if needed.

Usage:
    python get_puuid_and_seed.py <summonerName> 

It reads RIOT_API_KEY from .env in project root.
"""
import sys, os, requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
RIOT_API_KEY = os.getenv('RIOT_API_KEY')
if not RIOT_API_KEY:
    print('RIOT_API_KEY not found in environment (.env file).')
    sys.exit(1)

REGION = 'euw1'  # change if needed
if len(sys.argv) < 2:
    print('Usage: python get_puuid_and_seed.py <summonerName>')
    sys.exit(1)

name = sys.argv[1]
url = "https://{region}.api.riotgames.com/lol/summoner/v4/summoners/by-name/{name}?api_key={key}".format(
    region=REGION, name=name, key=RIOT_API_KEY
)
r = requests.get(url)
if r.status_code != 200:
    print('Error fetching summoner:', r.status_code, r.text)
    sys.exit(1)

data = r.json()
puuid = data.get('puuid')
if not puuid:
    print('PUUID not found in response.')
    sys.exit(1)

# connect to mongodb running in docker (host: localhost, default port)
mongo = MongoClient('mongodb://localhost:27017')
db = mongo['riot']
db.summoners.update_one({'summonerName': name}, {'$set': {'summonerName': name, 'puuid': puuid}}, upsert=True)
print('Seeded summoner {name} with puuid {puuid}'.format(name=name, puuid=puuid))
