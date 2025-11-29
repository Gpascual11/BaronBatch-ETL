FROM python:3.11-alpine

WORKDIR /app

RUN apk add --no-cache tzdata

RUN pip install --no-cache-dir streamlit requests pandas python-dotenv

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
