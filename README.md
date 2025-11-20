# 🎮 LoL Pro Grid - Advanced ETL Pipeline

> **A fully automated League of Legends data analysis system based on Docker microservices.**

This project implements a professional **ETL (Extract, Transform, Load)** architecture to monitor professional players, analyze their matches, and visualize advanced statistics in real-time.

-----

## 🏗️ System Architecture

The system is divided into **5 Docker containers** that work asynchronously and independently:

| Service | Technology | Main Function |
| :--- | :--- | :--- |
| **`dashboard`** | Streamlit (Python) | **Frontend.** User interface to search for players and view statistics. |
| **`api_service`** | FastAPI | **Read Layer.** Manages player registration and serves clean data to the dashboard. |
| **`extractor`** | Python + Requests | **Extract (E).** Downloads data from Riot Games (Matches, Profiles, Ranks) and handles Rate Limits. |
| **`transformer_loader`** | Python + Pandas | **Transform & Load (T/L).** Processes raw JSONs, calculates KDA/Stats, and saves clean data. |
| **`db`** | MongoDB | **Storage.** NoSQL database storing both raw and processed data. |

### 🔄 Data Flow (Pipeline)

1.  **Trigger (Registration):** The user adds a player (e.g., `Agurin#DND`) via the Dashboard.
2.  **Validation:** The `api_service` connects to Riot (Account-V1) to obtain the `PUUID` and automatically determine the region (EUW, KR, NA...).
3.  **Intelligent Extraction:** The `extractor` wakes up and starts the process:
      * Downloads the last 50 matches.
      * **Smart ID Recovery:** If the profile API fails (common with old accounts), it "steals" the summoner ID from within the match data.
      * **Rank Discovery (Plan C):** If the player is Challenger/Grandmaster and the standard API doesn't find them, it scans massive lists (`league-exp-v4`) until found.
4.  **Transformation:** The `transformer_loader` detects new raw files in the DB, calculates statistics (Winrate, CS/min, Items), and generates the final data model.
5.  **Visualization:** The Dashboard displays data with official icons (DDragon/CommunityDragon) and charts.

-----

## 🚀 How to Start the Project

### Prerequisites

  * **Docker** and **Docker Compose** installed.
  * A valid **Riot API Key** (get one at [developer.riotgames.com](https://developer.riotgames.com)).

### 1\. Configuration

Create a `.env` file at the root of the project with your key:

```bash
RIOT_API_KEY=RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

### 2\. Deployment

Open a terminal in the project folder and run:

```bash
# Build and start all containers in the background
docker-compose up -d --build
```

### 3\. Access

Open your browser and go to:
👉 **[http://localhost:8501](https://www.google.com/search?q=http://localhost:8501)**

-----

## 🧰 Command Toolbox (Cheat Sheet)

### 📡 Docker Management

Control the state of your containers.

| Action | Command |
| :--- | :--- |
| **Start everything (with build)** | `docker-compose up -d --build` |
| **View status** | `docker ps` |
| **Stop and clean networks** | `docker-compose down` |
| **Restart specific services** | `docker-compose restart api_service extractor` |
| **View logs in real-time** | `docker logs -f extractor` (or `api_service`, `transformer_loader`) |

### 🐍 Local Utility Scripts (`/utils` folder)

Tools for debugging or manual actions without Docker.
*(Requires `pip install requests pymongo python-dotenv` locally)*

| Script | Description | Typical Usage |
| :--- | :--- | :--- |
| `check_rank.py` | **Diagnosis.** Connects directly to Riot to see what the API really returns for a player. | `python utils/check_rank.py` |
| `clean_db.py` | **Cleanup.** Removes duplicate players or corrupt data from the database. | `python utils/clean_db.py` |
| `seed_player.py` | **Manual Entry.** Adds a player to the DB without using the Dashboard. | `python utils/seed_player.py` |
| `get_puuid_and_seed.py` | **Debug.** Just gets the PUUID and prints it. | `python utils/get_puuid.py` |

### 💾 Database Management (MongoDB)

How to enter the system's "guts" to see real data.

**1. Enter Mongo console (Inside Docker):**

```bash
docker exec -it db mongosh
```

**2. Basic Mongo Commands (`mongosh`):**

| Action | Mongo Command |
| :--- | :--- |
| **Select DB** | `use riot` |
| **View collections** | `show collections` |
| **Count downloaded matches** | `db.matches_raw.countDocuments()` |
| **Count clean matches** | `db.matches_clean.countDocuments()` |
| **View a player** | `db.summoners.find({summonerName: "Agurin#DND"}).pretty()` |
| **Delete clean data** | `db.matches_clean.drop()` |
| **Reset processing** | `db.matches_raw.updateMany({}, {$set: {processed: false}})` |
| **Delete everything (Nuclear)** | `db.dropDatabase()` |

-----

## 🛠️ Key Features ("The Cool Stuff")

  * **🌍 Automatic Multi-Region:** The system detects if the player is Korean (`#KR1`), American (`#NA1`), or European just by looking at the Tag. No configuration needed.
  * **🛡️ Anti-Blocking System (Rate Limits):** If Riot sends a 429 error (Too Many Requests), the extractor automatically enters "sleep" mode for 2 minutes and resumes work afterward.
  * **🕵️‍♂️ "Impossible" Rank Extraction:** Uses three strategies (Plan A, B, and C) to find the rank of high-Elo players that the standard API often hides.
  * **🎨 Robust Images:** Uses a combination of **DDragon** (Official) and **CommunityDragon** to ensure we always have item and champion images, even if names change (e.g., Wukong vs MonkeyKing).
  * **⏱️ Automation:** Includes an internal scheduler (`APScheduler`) that updates data every 30 minutes without human intervention.

-----

## 🚑 Common Issues Troubleshooting

### 1\. "Dashboard doesn't load (Connection Refused)"

If you just started your computer, the Database might take a few seconds to wake up.

  * **Solution:** Wait 10 seconds and refresh. If not working: `docker-compose restart api_service`.

### 2\. "Images not showing (Broken Image)"

If you are on a restrictive network (like **Eduroam** or offices), some CDNs might be blocked or DNS might fail.

  * **Solution:** The current code uses official Riot routes (DDragon) which are usually allowed. Ensure you have stable internet.

### 3\. "Rank shows Unranked but is Challenger"

The extractor may take a few minutes to do the massive sweep of the Challenger league.

  * **Solution:** Force a manual update by visiting: `http://localhost:8001/trigger_extract?count=50` and wait 2 minutes.

-----

## 📂 File Structure

```
riot_etl_project/
├── docker-compose.yml      # Service orchestrator
├── .env                    # Environment variables (Secret)
├── utils/                  # Local helper scripts
│   ├── check_rank.py       # Rank diagnosis
│   └── clean_db.py         # Duplicate cleanup
├── api_service/            # API (FastAPI)
│   ├── Dockerfile
│   └── main.py
├── extractor/              # ETL Extract (Python)
│   ├── Dockerfile
│   └── main.py             # Complex Riot API logic
├── transformer_loader/     # ETL Transform (Python)
│   ├── Dockerfile
│   └── main.py             # Data cleaning and calculations
└── dashboard/              # Frontend (Streamlit)
    ├── Dockerfile
    └── app.py              # Graphical interface
```

-----

*Created with ❤️, Python, and lots of patience with the Riot API.*