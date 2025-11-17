# Riot ETL - Ready Project (Light OP.GG Dashboard)

## Overview
This project contains a minimal ETL stack for Riot Games match data:
- MongoDB
- Extractor (FastAPI)
- Transformer/Loader (FastAPI)
- API service (FastAPI)
- Dashboard (Streamlit)

This ZIP was prepared per your choices:
- Dashboard: Light OP.GG-like (1B)
- Scheduler: NOT included (2C) — you will trigger extract/process manually or add a scheduler later
- .env: RIOT_API_KEY included (from user input) — **rotate this key after use for security**

## Quick start

1. Make sure Docker is installed.
2. Place this project on your machine and `cd` into the project folder.
3. (Optional) Edit `.env` if you want to change API key.
4. Build and start:

```bash
docker compose up --build
```

Services:
- MongoDB: localhost:27017
- Extractor: localhost:8001
- Transformer: localhost:8002
- API Service: localhost:8003
- Dashboard (Streamlit): localhost:8501

## Seed a summoner (get PUUID and insert into DB)
From your local machine (Python required), run:

```bash
python utils/get_puuid_and_seed.py <SummonerName>
```

This will insert a document into `riot.summoners`.

## Security note
You provided a Riot API key which is included in `.env`. Treat this key like a password:
- do not commit it to public git
- rotate the key in Riot developer portal after development

