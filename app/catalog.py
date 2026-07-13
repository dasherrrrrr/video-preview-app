"""
Katalog-Scan: liest das Videoverzeichnis ein und hält die videos-Tabelle
mit dem Dateisystem synchron. Nutzt ffprobe (kommt mit ffmpeg) für Metadaten -
kein zusätzliches Python-Paket nötig, ffprobe wird als Subprozess aufgerufen.
"""

import json
import os
import subprocess
from pathlib import Path

from .database import get_db
from .thumbnails import generate_thumbnail, get_thumbnail_path
from .transcode import get_cache_path as get_transcode_cache_path

VIDEOS_DIR = Path(os.environ.get("VIDEOS_DIR", "/videos"))

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


def _probe(filepath: Path) -> dict:
    """Fragt Dauer und Video-Codec über ffprobe ab. Gibt leere Werte zurück,
    falls ffprobe die Datei nicht lesen kann (z.B. kaputte/unvollständige Datei)."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration:stream=codec_type,codec_name",
                "-of", "json",
                str(filepath),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return {"duration_seconds": None, "codec": None}

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"duration_seconds": None, "codec": None}

    duration = None
    if "format" in data and data["format"].get("duration"):
        duration = float(data["format"]["duration"])

    codec = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            codec = stream.get("codec_name")
            break

    return {"duration_seconds": duration, "codec": codec}


def scan_library() -> dict:
    """Läuft durch VIDEOS_DIR, legt neue Videos in der DB an, probet sie mit
    ffprobe und entfernt DB-Einträge für Dateien, die nicht mehr existieren.
    Gibt eine Zusammenfassung zurück (für die Admin-Oberfläche)."""
    found_paths = set()
    added = 0
    skipped_existing = 0

    if VIDEOS_DIR.is_dir():
        for path in sorted(VIDEOS_DIR.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            rel_path = str(path.relative_to(VIDEOS_DIR))
            found_paths.add(rel_path)

            with get_db() as conn:
                existing = conn.execute(
                    "SELECT id, duration_seconds FROM videos WHERE filepath = ?", (rel_path,)
                ).fetchone()
                if existing:
                    skipped_existing += 1
                    # Nachziehen für Videos, die vor Einführung des Thumbnail-Features
                    # gescannt wurden - kein erneutes ffprobe nötig.
                    if not get_thumbnail_path(existing["id"]).is_file():
                        generate_thumbnail(existing["id"], path, existing["duration_seconds"])
                    continue

                meta = _probe(path)
                cursor = conn.execute(
                    "INSERT INTO videos (filepath, title, duration_seconds, codec) "
                    "VALUES (?, ?, ?, ?)",
                    (rel_path, path.stem, meta["duration_seconds"], meta["codec"]),
                )
                generate_thumbnail(cursor.lastrowid, path, meta["duration_seconds"])
                added += 1

    with get_db() as conn:
        existing = conn.execute("SELECT id, filepath FROM videos").fetchall()
        removed = 0
        for row in existing:
            if row["filepath"] not in found_paths:
                conn.execute("DELETE FROM videos WHERE id = ?", (row["id"],))
                get_thumbnail_path(row["id"]).unlink(missing_ok=True)
                get_transcode_cache_path(row["id"]).unlink(missing_ok=True)
                removed += 1

    return {"added": added, "removed": removed, "unchanged": skipped_existing}
