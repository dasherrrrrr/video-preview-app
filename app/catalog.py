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
from .thumbnails import generate_thumbnail, get_marker_frame_path, get_thumbnail_path
from .transcode import get_cache_path as get_transcode_cache_path

VIDEOS_DIR = Path(os.environ.get("VIDEOS_DIR", "/videos"))

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


def _probe(filepath: Path) -> dict:
    """Fragt Dauer, Video-Codec, Breite und Bitrate über ffprobe ab. Bitrate/
    Breite werden zusätzlich zum Codec gebraucht, weil manche Kamera-/Drohnen-
    Originale zwar H.264 ("browser-kompatibel") sind, aber mit Bitraten von
    100+ Mbit/s in 4K - das ruckelt auf praktisch jeder Verbindung, siehe
    transcode.needs_transcode(). Gibt leere Werte zurück, falls ffprobe die
    Datei nicht lesen kann (z.B. kaputte/unvollständige Datei)."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration,bit_rate:stream=codec_type,codec_name,width,bit_rate",
                "-of", "json",
                str(filepath),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return {"duration_seconds": None, "codec": None, "bit_rate": None, "width": None}

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"duration_seconds": None, "codec": None, "bit_rate": None, "width": None}

    duration = None
    if "format" in data and data["format"].get("duration"):
        duration = float(data["format"]["duration"])

    # Gesamt-Bitrate (Container-Ebene) als Fallback, falls der Video-Stream
    # selbst keine eigene Bitrate meldet (bei manchen Containern/Codecs üblich).
    format_bit_rate = None
    if "format" in data and data["format"].get("bit_rate"):
        format_bit_rate = int(data["format"]["bit_rate"])

    codec = None
    width = None
    stream_bit_rate = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            codec = stream.get("codec_name")
            width = stream.get("width")
            if stream.get("bit_rate"):
                stream_bit_rate = int(stream["bit_rate"])
            break

    return {
        "duration_seconds": duration,
        "codec": codec,
        "bit_rate": stream_bit_rate or format_bit_rate,
        "width": width,
    }


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
                    "SELECT id, duration_seconds, bit_rate FROM videos WHERE filepath = ?", (rel_path,)
                ).fetchone()
                if existing:
                    skipped_existing += 1
                    # Nachziehen für Videos, die vor Einführung des Thumbnail-
                    # bzw. Bitrate/Breite-Features gescannt wurden.
                    if not get_thumbnail_path(existing["id"]).is_file():
                        generate_thumbnail(existing["id"], path, existing["duration_seconds"])
                    if existing["bit_rate"] is None:
                        meta = _probe(path)
                        conn.execute(
                            "UPDATE videos SET bit_rate = ?, width = ? WHERE id = ?",
                            (meta["bit_rate"], meta["width"], existing["id"]),
                        )
                    continue

                meta = _probe(path)
                cursor = conn.execute(
                    "INSERT INTO videos (filepath, title, duration_seconds, codec, bit_rate, width) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (rel_path, path.stem, meta["duration_seconds"], meta["codec"], meta["bit_rate"], meta["width"]),
                )
                generate_thumbnail(cursor.lastrowid, path, meta["duration_seconds"])
                added += 1

    with get_db() as conn:
        existing = conn.execute("SELECT id, filepath FROM videos").fetchall()
        removed = 0
        for row in existing:
            if row["filepath"] not in found_paths:
                marker_ids = [
                    m["id"]
                    for m in conn.execute(
                        "SELECT id FROM markers WHERE video_id = ?", (row["id"],)
                    ).fetchall()
                ]
                conn.execute("DELETE FROM videos WHERE id = ?", (row["id"],))
                get_thumbnail_path(row["id"]).unlink(missing_ok=True)
                get_transcode_cache_path(row["id"]).unlink(missing_ok=True)
                for marker_id in marker_ids:
                    get_marker_frame_path(marker_id).unlink(missing_ok=True)
                removed += 1

    # Transkodiert wird absichtlich nicht mehr hier: bei mehreren tausend
    # Dateien in der Bibliothek wäre das Vortranskodieren aller Videos beim
    # Scan viel zu teuer (CPU/GPU, Speicher, Laufzeit), obwohl die meisten nie
    # einem Kunden zugewiesen werden. Stattdessen wird pro Video erst dann
    # transkodiert, wenn es einem Kunden zugewiesen wird (siehe
    # customers.set_permissions) - mit /api/stream als Fallback, falls ein
    # Video aufgerufen wird, bevor der Zuweisungs-Transcode fertig ist.
    return {
        "added": added,
        "removed": removed,
        "unchanged": skipped_existing,
        "transcoded": 0,
        "transcode_failed": 0,
    }
