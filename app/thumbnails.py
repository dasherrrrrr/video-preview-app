"""Erzeugt ein Vorschaubild pro Video beim Katalog-Scan (einmalig, danach
aus dem Cache bedient). Nutzt ffmpeg, um einen einzelnen Frame zu extrahieren -
kein zusätzliches Python-Paket nötig."""

import subprocess
from pathlib import Path

from .database import DB_PATH

THUMBNAIL_CACHE_DIR = DB_PATH.parent / "thumbnails"


def get_thumbnail_path(video_id: int) -> Path:
    return THUMBNAIL_CACHE_DIR / f"{video_id}.jpg"


def generate_thumbnail(video_id: int, source_path: Path, duration_seconds: float | None) -> None:
    """Schreibt ein JPEG-Vorschaubild für das Video. Schlägt lautlos fehl
    (kein Bild), falls ffmpeg die Datei nicht lesen kann - der Katalog-Scan
    soll daran nicht scheitern."""
    THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = get_thumbnail_path(video_id)

    # Frame aus den ersten 10% des Videos (nie später als 5s) - meist schon
    # ein brauchbares Bild, ohne bei kurzen Clips über das Ende zu laufen.
    seek = min((duration_seconds or 0) * 0.1, 5.0)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(seek),
        "-i", str(source_path),
        "-frames:v", "1",
        "-vf", "scale=480:-1",
        "-q:v", "4",
        str(dest_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        dest_path.unlink(missing_ok=True)


def set_custom_thumbnail(video_id: int, upload_path: Path) -> None:
    """Ersetzt das Vorschaubild durch ein von einem Admin hochgeladenes
    PNG/JPG. Läuft durch ffmpeg, damit am Ende immer dasselbe Format/dieselbe
    Größe wie bei automatisch erzeugten Thumbnails rauskommt - der
    Streaming-Endpoint muss also nicht zwischen beiden unterscheiden.
    Ein manuell gesetztes Thumbnail bleibt bei künftigen Katalog-Scans
    unangetastet (die prüfen nur, ob die Datei schon existiert)."""
    THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = get_thumbnail_path(video_id)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(upload_path),
        "-frames:v", "1",
        "-vf", "scale=480:-1",
        "-q:v", "4",
        str(dest_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    except subprocess.CalledProcessError as exc:
        raise ValueError("Datei konnte nicht als Bild gelesen werden.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Zeitlimit beim Verarbeiten des Bilds überschritten.") from exc
