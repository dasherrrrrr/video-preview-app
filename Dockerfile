FROM python:3.12-slim

WORKDIR /app

# ffprobe (Teil von ffmpeg) wird für den Katalog-Scan gebraucht, um Dauer
# und Codec der Videodateien auszulesen.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Erst nur requirements.txt kopieren, damit Docker diesen Layer cachen kann
# und nicht bei jeder Code-Änderung alle Python-Pakete neu installiert
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Hier landet die SQLite-Datenbank - als Volume mounten, sonst ist sie
# beim nächsten Container-Update weg!
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
