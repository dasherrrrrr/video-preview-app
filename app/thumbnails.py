"""Erzeugt ein Vorschaubild pro Video beim Katalog-Scan (einmalig, danach
aus dem Cache bedient). Nutzt ffmpeg, um einen einzelnen Frame zu extrahieren -
kein zusätzliches Python-Paket nötig."""

import subprocess
from pathlib import Path

from .database import DB_PATH

THUMBNAIL_CACHE_DIR = DB_PATH.parent / "thumbnails"
MARKER_FRAME_CACHE_DIR = DB_PATH.parent / "marker_frames"
PHOTO_THUMBNAIL_CACHE_DIR = DB_PATH.parent / "photo_thumbnails"


def get_thumbnail_path(video_id: int) -> Path:
    return THUMBNAIL_CACHE_DIR / f"{video_id}.jpg"


def get_photo_thumbnail_path(photo_id: int) -> Path:
    return PHOTO_THUMBNAIL_CACHE_DIR / f"{photo_id}.jpg"


def generate_photo_thumbnail(photo_id: int, source_path: Path) -> None:
    """Schreibt eine verkleinerte JPEG-Vorschau eines Fotos - dieselbe
    ffmpeg-basierte Herangehensweise wie bei Video-Thumbnails, funktioniert
    auch für Bilddateien (auch HEIC/HEIF von iPhones). Schlägt lautlos fehl
    (kein Bild), falls ffmpeg die Datei nicht lesen kann."""
    PHOTO_THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = get_photo_thumbnail_path(photo_id)
    cmd = [
        "ffmpeg", "-y",
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


def get_marker_frame_path(marker_id: int) -> Path:
    return MARKER_FRAME_CACHE_DIR / f"{marker_id}.jpg"


def generate_marker_frame(marker_id: int, source_path: Path, timestamp_seconds: float) -> None:
    """Schreibt ein JPEG des exakten Frames zum Marker-Zeitpunkt - das
    generische Video-Thumbnail (siehe generate_thumbnail) ist an einem festen
    frühen Zeitpunkt und passt daher meist nicht zu dem Frame, auf den sich
    eine Kreis-/Pfeil-Markierung tatsächlich bezieht (das Motiv kann sich bis
    dahin ja schon bewegt/geändert haben). Schlägt lautlos fehl (kein Bild),
    das Overlay fällt dann in der Vorlage auf das generische Thumbnail
    zurück.

    Zweistufiges Seeking: ein grobes -ss VOR -i (schnell, springt aber nur
    zum nächsten Keyframe - bei Kamera-Originalen mit mehrsekündigen
    Keyframe-Abständen kann das spürbar neben dem angeklickten Zeitpunkt
    liegen) plus ein kleines, präzises -ss NACH -i (dekodiert die paar
    Frames bis zum exakten Zeitpunkt) - liefert den exakten Frame, ohne bei
    langen Videos vom Dateianfang an dekodieren zu müssen."""
    MARKER_FRAME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = get_marker_frame_path(marker_id)
    target = max(timestamp_seconds, 0)
    coarse_seek = max(target - 2.0, 0)
    precise_seek = target - coarse_seek
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(coarse_seek),
        "-i", str(source_path),
        "-ss", str(precise_seek),
        "-frames:v", "1",
        "-vf", "scale=480:-1",
        "-q:v", "4",
        str(dest_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        dest_path.unlink(missing_ok=True)


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
