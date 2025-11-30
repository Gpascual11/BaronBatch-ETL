FROM python:3.11-alpine

WORKDIR /app

RUN apk add --no-cache tzdata

ENV TZ=Europe/Madrid

RUN pip install --no-cache-dir fastapi uvicorn pymongo python-dotenv requests redis>=5.0.0

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
