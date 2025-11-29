FROM python:3.11-alpine

WORKDIR /app

RUN apk add --no-cache tzdata

RUN pip install --no-cache-dir fastapi uvicorn pymongo requests python-dotenv apscheduler

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
