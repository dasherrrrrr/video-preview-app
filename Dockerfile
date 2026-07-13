FROM python:3.12-slim

WORKDIR /app

# ffprobe (Teil von ffmpeg) wird für den Katalog-Scan gebraucht, um Dauer
# und Codec der Videodateien auszulesen. intel-media-va-driver-non-free liefert
# den VAAPI-Treiber (iHD) für Hardware-Transcoding auf Intel-GPUs (z.B. Arc A380) -
# liegt im "non-free"-Repo, daher wird das Component-Set erweitert.
RUN sed -i 's/Components: main/Components: main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg intel-media-va-driver-non-free vainfo \
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
