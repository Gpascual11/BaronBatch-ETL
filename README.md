🎮 LoL Pro Grid - Advanced ETL Pipeline

A fully automated League of Legends data analysis system based on Docker microservices.

This project implements a professional ETL (Extract, Transform, Load) architecture to monitor professional players, analyze their matches, and visualize advanced statistics in real-time.

🏗️ System Architecture

The system is divided into 5 Docker containers that work asynchronously and independently:

Service

Technology

Main Function

dashboard

Streamlit (Python)

Frontend. User interface to search for players and view statistics.

api_service

FastAPI

Read Layer & Orchestrator. Manages player registration, distributes tasks to Redis, and serves clean data.

extractor

Python + Requests

Extract (E). Worker nodes that download data from Riot (Matches, Profiles) handling Rate Limits & Encryption.

transformer_loader

Python + Pandas

Transform & Load (T/L). Processes raw JSONs, normalizes player IDs, calculates KDA/Stats, and saves clean data.

db

MongoDB

Storage. NoSQL database storing both raw JSON responses and processed analytical data.

redis

Redis

Message Queue. Buffer between API and Extractors to manage load balancing.

🔄 Data Flow (Pipeline)

Trigger (Registration): The user adds a player (e.g., Agurin#DND) via the Dashboard.

Validation & Routing: The api_service connects to Riot (Account-V1) to obtain the PUUID and automatically determine the region (EUW, KR, NA...).

Task Distribution: The api_service splits the history fetch into batches (e.g., 0-50, 50-100) and pushes tasks to Redis.

Intelligent Extraction: The extractor workers pick up tasks from Redis:

Load Balancing: Multiple extractors work in parallel using different API Keys.

Key Mismatch Resolution: Detects if a PUUID is encrypted for a different key and automatically resolves a local ID to continue processing.

Anti-Blocking: Automatically sleeps when hitting Rate Limits (429).

Transformation: The transformer_loader detects new raw files in the DB, identifies the correct player (even with missing tags), calculates statistics, and generates the final data model.

Visualization: The Dashboard displays data with official icons (DDragon/CommunityDragon) and interactive charts.

🚀 How to Start the Project

Prerequisites

Docker and Docker Compose installed.

A valid Riot API Key (get one at developer.riotgames.com).

1. Configuration

Create a .env file at the root of the project with your keys:

# You can use two different keys for double the speed, or the same key twice
RIOT_API_KEY_1=RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
RIOT_API_KEY_2=RGAPI-yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy


2. Deployment

Open a terminal in the project folder and run:

# Build and start all containers in the background
docker-compose up -d --build


3. Access

Open your browser and go to:
👉 http://localhost:8501

🧰 Command Toolbox (Cheat Sheet)

📡 Docker Management

Control the state of your containers.

Action

Command

Start everything (with build)

docker-compose up -d --build

View status

docker ps

Stop and clean networks

docker-compose down

Restart specific services

docker-compose restart api_service extractor_1

View logs in real-time

docker logs -f extractor_1 (or api_service, transformer_loader)

💾 Database Management (MongoDB)

How to enter the system's "guts" to see real data.

1. Enter Mongo console (Inside Docker):

docker exec -it db mongosh


2. Basic Mongo Commands (mongosh):

Action

Mongo Command

Select DB

use riot

View collections

show collections

Count downloaded matches

db.matches_raw.countDocuments()

Count clean matches

db.matches_clean.countDocuments()

View a player

db.summoners.find({summonerName: "Agurin#DND"}).pretty()

Delete clean data

db.matches_clean.drop()

Reset processing

db.matches_raw.updateMany({}, {$set: {processed: false}})

Delete everything (Nuclear)

db.dropDatabase()

🛠️ Key Features ("The Cool Stuff")

🌍 Automatic Multi-Region: The system detects if the player is Korean (#KR1), American (#NA1), or European just by looking at the Tag. No configuration needed.

🛡️ Anti-Blocking System (Rate Limits): If Riot sends a 429 error (Too Many Requests), the extractor automatically enters "sleep" mode for 2 minutes and resumes work afterward.

🔑 Distributed Multi-Key Support: Uses multiple extractors with different API keys. Includes logic to "translate" encrypted IDs between keys on the fly.

🕵️‍♂️ "Impossible" Rank Extraction: Uses three strategies (Plan A, B, and C) to find the rank of high-Elo players that the standard API often hides.

🎨 Robust Images: Uses a combination of DDragon (Official) and CommunityDragon to ensure we always have item and champion images, even if names change (e.g., Wukong vs MonkeyKing).

⏱️ Automation: Includes internal schedulers (APScheduler) in both Extractor and Transformer to update data and process statistics automatically.

🚑 Common Issues Troubleshooting

1. "Dashboard doesn't load (Connection Refused)"

If you just started your computer, the Database might take a few seconds to wake up.

Solution: Wait 10 seconds and refresh. If not working: docker-compose restart api_service.

2. "Images not showing (Broken Image)"

If you are on a restrictive network (like Eduroam or offices), some CDNs might be blocked or DNS might fail.

Solution: The current code uses official Riot routes (DDragon) which are usually allowed. Ensure you have stable internet.

3. "Rank shows Unranked but is Challenger"

The extractor may take a few minutes to do the massive sweep of the Challenger league if the standard endpoint fails.

Solution: Force a manual update via the Dashboard "Update" button and check logs: docker logs -f extractor_1.

📂 File Structure

riot_etl_project/
├── docker-compose.yml      # Service orchestrator
├── .env                    # Environment variables (Secret)
├── api_service/            # API (FastAPI) & Orchestrator
│   ├── Dockerfile
│   └── main.py
├── extractor/              # ETL Extract (Python Worker)
│   ├── Dockerfile
│   └── main.py             # Complex Riot API logic
├── transformer_loader/     # ETL Transform (Python Processor)
│   ├── Dockerfile
│   └── main.py             # Data cleaning and calculations
└── dashboard/              # Frontend (Streamlit)
    ├── Dockerfile
    └── app.py              # Graphical interface


Created with ❤️, Python, and lots of patience with the Riot API.