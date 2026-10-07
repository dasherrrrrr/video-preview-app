"""
Katalog-Scan: liest das Videoverzeichnis ein und hält die videos-Tabelle
mit dem Dateisystem synchron. Nutzt ffprobe (kommt mit ffmpeg) für Metadaten -
kein zusätzliches Python-Paket nötig, ffprobe wird als Subprozess aufgerufen.
"""

import json
import os
import sqlite3
import subprocess
import threading
from pathlib import Path

from .database import get_connection
from .thumbnails import (
    generate_photo_thumbnail,
    generate_thumbnail,
    get_marker_frame_path,
    get_photo_thumbnail_path,
    get_thumbnail_path,
)
from .transcode import (
    get_cache_metadata_path as get_transcode_cache_metadata_path,
    get_cache_path as get_transcode_cache_path,
)

# Verhindert, dass zwei Scans gleichzeitig laufen (z.B. weil ein Client nach
# einem 504-Timeout des Reverse-Proxys den Scan für erneut fehlgeschlagen
# hält und ihn nochmal auslöst, während der ursprüngliche Request serverseitig
# unbeeindruckt weiterläuft - die Scan-Funktionen selbst laufen synchron zu
# Ende, egal ob der ursprüngliche Client noch verbunden ist). Ohne diese
# Sperre versuchen beide Durchläufe dieselbe neu gefundene Datei einzutragen
# und der zweite crasht mit einem UNIQUE-constraint-Fehler.
_scan_lock = threading.Lock()

VIDEOS_DIR = Path(os.environ.get("VIDEOS_DIR", "/videos"))
# Der eigentliche Archiv-Mount bleibt bewusst read-only. Nur zum Anlegen der
# kundenbezogenen Fotos-Ordner nutzen wir den zweiten, beschreibbaren Mount
# desselben Host-Verzeichnisses.
VIDEOS_WRITE_DIR = Path(os.environ.get("VIDEOS_WRITE_DIR", "/videos-rw"))

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff"}


def ensure_photo_folders(folders: list[str]) -> list[str]:
    """Legt sichere, relative Kunden-Fotoordner im beschreibbaren Archiv-Mount an.

    Die Ordnernamen stammen aus Concordes Video-Ordnerzuordnung und werden
    trotzdem strikt gegen absolute Pfade und Traversal geprüft.
    """
    root = VIDEOS_WRITE_DIR.resolve()
    created: list[str] = []
    for folder in folders:
        rel = Path(folder.strip().strip("/"))
        if not folder or rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
            raise ValueError("Ungültiger Foto-Ordnerpfad.")
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            raise ValueError("Ungültiger Foto-Ordnerpfad.")
        target.mkdir(parents=True, exist_ok=True)
        created.append(str(rel))
    return created

# Der Archiv-Ordnerbaum enthält neben echten Kundenfotos auch jede Menge
# Icons/Logos/Screenshots (Software-Assets, interne Dokumente) - alles unter
# dieser Kantenlänge (Breite UND Höhe) ist praktisch nie ein echtes Foto und
# wird beim Scan ignoriert. Reine Auflösungs-Heuristik, kein Ordner-Filter -
# welcher Ordner tatsächlich Kundenfotos enthält, wählt der Admin selbst aus
# (siehe folder-Feld in api_admin.list_photo_catalog).
MIN_PHOTO_DIMENSION = 200

# Wie oft während eines Scans committed wird (siehe _scan_library_impl/
# _scan_photos_impl) - eine Verbindung für den ganzen Scan statt einer neuen
# SQLite-Verbindung pro Datei (bei >100.000 Dateien war allein das Öffnen/
# Schließen einer Verbindung pro Datei der dominierende Zeitfaktor, nicht
# ffprobe). Bewusst klein gehalten: jede offene Schreib-Transaktion blockiert
# unter WAL zwar keine Leser, aber sehr wohl andere Schreiber - und
# api_auth.require_api_token schreibt bei JEDER Token-Anfrage (last_used_at),
# ist also selbst ein Schreiber. Ein zu hoher Wert hier führte zu
# "database is locked" für praktisch jeden Concorde-Request während eines
# laufenden Scans. Bei 10 bleibt die Schreibsperre pro Commit-Fenster kurz
# genug, dass busy_timeout (siehe database.get_connection) sie zuverlässig
# überbrückt, während trotzdem nur 1/10 der ursprünglichen Verbindungen
# geöffnet werden muss.
COMMIT_EVERY = 10


def _probe(filepath: Path) -> dict:
    """Fragt Dauer, Video-Codec, Breite und Bitrate über ffprobe ab (Bitrate/
    Breite sind reine Katalog-Info, für die Transcode-Entscheidung selbst
    inzwischen ohne Belang - jedes zugewiesene Video wird einheitlich auf
    720p/6 Mbit gebracht, siehe transcode.ensure_transcoded()). Gibt leere
    Werte zurück, falls ffprobe die Datei nicht lesen kann (z.B. kaputte/
    unvollständige Datei)."""
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


def _source_signature(filepath: Path) -> tuple[int, int, int]:
    """Schnelle Versionserkennung für eine Quelldatei.

    Größe, Nanosekunden-mtime und Inode erkennen die übliche Exportroutine
    (temporäre Datei schreiben und anschließend umbenennen), ohne beim Scan
    jede Videodatei vollständig hashen zu müssen.
    """
    stat = filepath.stat()
    return (
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(getattr(stat, "st_ino", 0)),
    )


def _invalidate_video_version(conn: sqlite3.Connection, video_id: int, filepath: Path, duration: float | None) -> None:
    """Entfernt Caches, die zu einer älteren Datei-Version gehören."""
    get_transcode_cache_path(video_id).unlink(missing_ok=True)
    get_transcode_cache_metadata_path(video_id).unlink(missing_ok=True)
    marker_rows = conn.execute(
        "SELECT id FROM markers WHERE video_id = ?", (video_id,)
    ).fetchall()
    for marker in marker_rows:
        get_marker_frame_path(marker["id"]).unlink(missing_ok=True)

    # Ein manuell gesetztes Thumbnail bleibt erhalten, sofern es neuer als die
    # Quelle ist. Automatische Thumbnails werden bei einer neuen Version
    # aktualisiert, sobald sie älter als die Quelldatei sind.
    thumbnail = get_thumbnail_path(video_id)
    try:
        refresh_thumbnail = not thumbnail.is_file() or thumbnail.stat().st_mtime_ns < filepath.stat().st_mtime_ns
    except OSError:
        refresh_thumbnail = False
    if refresh_thumbnail:
        generate_thumbnail(video_id, filepath, duration)


def scan_library(folders: list[str] | None = None) -> dict:
    """Öffentlicher Einstiegspunkt - siehe _scan_lock oben, warum das nicht
    einfach die Scan-Logik selbst ist."""
    with _scan_lock:
        return _scan_library_scoped(folders) if folders else _scan_library_impl()


def _scan_library_impl() -> dict:
    """Läuft durch VIDEOS_DIR, legt neue Videos in der DB an, probet sie mit
    ffprobe und entfernt DB-Einträge für Dateien, die nicht mehr existieren.
    Gibt eine Zusammenfassung zurück (für die Admin-Oberfläche)."""
    # Ein nicht eingehängter SMB/NFS-Mount darf niemals als leeres Archiv
    # interpretiert werden. Andernfalls würde der Aufräumloop alle vorhandenen
    # Katalogeinträge als entfernt markieren.
    if not VIDEOS_DIR.is_dir():
        raise RuntimeError("Medien-Mount ist nicht erreichbar; Katalog bleibt unverändert.")

    found_paths = set()
    found_folders = set()
    added = 0
    skipped_existing = 0
    folders_scanned = False

    conn = get_connection()
    try:
        if VIDEOS_DIR.is_dir():
            folders_scanned = True
            processed = 0
            for path in sorted(VIDEOS_DIR.rglob("*")):
                if path.is_dir():
                    rel_folder = str(path.relative_to(VIDEOS_DIR))
                    if rel_folder and rel_folder != ".":
                        found_folders.add(rel_folder)
                    continue
                if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                try:
                    source_size, source_mtime_ns, source_inode = _source_signature(path)
                except OSError:
                    # Datei wurde während des Scans verschoben/ersetzt.
                    continue
                rel_path = str(path.relative_to(VIDEOS_DIR))
                found_paths.add(rel_path)

                existing = conn.execute(
                    "SELECT id, duration_seconds, codec, bit_rate, width, "
                    "source_size, source_mtime_ns, source_inode "
                    "FROM videos WHERE filepath = ?", (rel_path,)
                ).fetchone()
                if existing:
                    skipped_existing += 1
                    signature_known = all(
                        existing[key] is not None
                        for key in ("source_size", "source_mtime_ns", "source_inode")
                    )
                    source_changed = signature_known and (
                        existing["source_size"], existing["source_mtime_ns"], existing["source_inode"]
                    ) != (source_size, source_mtime_ns, source_inode)
                    if source_changed:
                        meta = _probe(path)
                        _invalidate_video_version(conn, existing["id"], path, meta["duration_seconds"])
                        conn.execute(
                            "UPDATE videos SET duration_seconds = ?, codec = ?, bit_rate = ?, width = ?, "
                            "source_size = ?, source_mtime_ns = ?, source_inode = ? WHERE id = ?",
                            (meta["duration_seconds"], meta["codec"], meta["bit_rate"], meta["width"],
                             source_size, source_mtime_ns, source_inode, existing["id"]),
                        )
                    elif not signature_known:
                        # Bestandsdaten einmalig mit einer Signatur versehen,
                        # ohne vorhandene manuelle Thumbnails zu überschreiben.
                        conn.execute(
                            "UPDATE videos SET source_size = ?, source_mtime_ns = ?, source_inode = ? WHERE id = ?",
                            (source_size, source_mtime_ns, source_inode, existing["id"]),
                        )
                    if not get_thumbnail_path(existing["id"]).is_file():
                        generate_thumbnail(existing["id"], path, existing["duration_seconds"])
                else:
                    meta = _probe(path)
                    try:
                        cursor = conn.execute(
                            "INSERT INTO videos (filepath, title, duration_seconds, codec, bit_rate, width, "
                            "source_size, source_mtime_ns, source_inode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (rel_path, path.stem, meta["duration_seconds"], meta["codec"], meta["bit_rate"],
                             meta["width"], source_size, source_mtime_ns, source_inode),
                        )
                    except sqlite3.IntegrityError:
                        # Sollte dank _scan_lock nicht mehr vorkommen, bleibt aber
                        # als zweite Absicherung stehen statt den ganzen Scan
                        # abzubrechen.
                        skipped_existing += 1
                    else:
                        generate_thumbnail(cursor.lastrowid, path, meta["duration_seconds"])
                        added += 1

                processed += 1
                if processed % COMMIT_EVERY == 0:
                    conn.commit()

            # Ordner werden bewusst separat vom Video-Katalog gespeichert.
            # So bleiben auch Verzeichnisse ohne Video (z.B. Audio, Rohmaterial
            # oder ein frisch angelegter Kundenordner) in der Auswahl sichtbar.
            # Nur nach einem erfolgreich begonnenen vollständigen Mount-Scan
            # wird die bisherige Ordnerliste ersetzt; bei einem fehlenden Mount
            # bleibt der letzte bekannte Stand erhalten.
            conn.execute("DELETE FROM media_folders")
            conn.executemany(
                "INSERT INTO media_folders (path) VALUES (?)",
                ((folder,) for folder in sorted(found_folders)),
            )

        existing_rows = conn.execute("SELECT id, filepath FROM videos").fetchall()
        # Auch ein vorhandener, aber kurzfristig leer gelieferter Mount darf
        # keinen destruktiven Komplettabgleich auslösen.
        if existing_rows and not found_paths:
            raise RuntimeError("Medien-Mount lieferte keine Dateien; Katalog bleibt unverändert.")
        removed = 0
        for row in existing_rows:
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
                get_transcode_cache_metadata_path(row["id"]).unlink(missing_ok=True)
                for marker_id in marker_ids:
                    get_marker_frame_path(marker_id).unlink(missing_ok=True)
                removed += 1
        conn.commit()
    finally:
        conn.close()

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
        "folders": len(found_folders) if folders_scanned else None,
    }


def _scan_library_scoped(folders: list[str]) -> dict:
    """Aktualisiert nur die angegebenen Kundenordner.

    Das ist die schnelle Variante für die Admin-Ansicht: neue Exporte werden
    sofort katalogisiert, ohne den kompletten Medien-Mount erneut zu prüfen.
    """
    normalized = [f.strip().strip("/") for f in folders if f and f.strip().strip("/")]
    if not normalized:
        return {"added": 0, "removed": 0, "unchanged": 0, "transcoded": 0, "transcode_failed": 0, "folders": 0}

    root = VIDEOS_DIR.resolve()
    scan_roots: list[Path] = []
    valid_prefixes: list[str] = []
    for folder in normalized:
        rel = Path(folder)
        if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
            raise ValueError("Ungültiger Video-Ordnerpfad.")
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            raise ValueError("Ungültiger Video-Ordnerpfad.")
        if target.is_dir():
            scan_roots.append(target)
            valid_prefixes.append(str(rel).replace("\\", "/"))

    if not scan_roots:
        return {"added": 0, "removed": 0, "unchanged": 0, "transcoded": 0, "transcode_failed": 0, "folders": 0}

    found_paths: set[str] = set()
    found_folders: set[str] = set(valid_prefixes)
    added = 0
    unchanged = 0
    conn = get_connection()
    try:
        processed = 0
        for scan_root in scan_roots:
            for path in sorted(scan_root.rglob("*")):
                if path.is_dir():
                    found_folders.add(str(path.relative_to(VIDEOS_DIR)).replace("\\", "/"))
                    continue
                if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                try:
                    source_size, source_mtime_ns, source_inode = _source_signature(path)
                except OSError:
                    continue
                rel_path = str(path.relative_to(VIDEOS_DIR)).replace("\\", "/")
                found_paths.add(rel_path)
                existing = conn.execute(
                    "SELECT id, duration_seconds, codec, bit_rate, width, "
                    "source_size, source_mtime_ns, source_inode "
                    "FROM videos WHERE filepath = ?", (rel_path,)
                ).fetchone()
                if existing:
                    unchanged += 1
                    signature_known = all(
                        existing[key] is not None
                        for key in ("source_size", "source_mtime_ns", "source_inode")
                    )
                    source_changed = signature_known and (
                        existing["source_size"], existing["source_mtime_ns"], existing["source_inode"]
                    ) != (source_size, source_mtime_ns, source_inode)
                    if source_changed:
                        meta = _probe(path)
                        _invalidate_video_version(conn, existing["id"], path, meta["duration_seconds"])
                        conn.execute(
                            "UPDATE videos SET duration_seconds = ?, codec = ?, bit_rate = ?, width = ?, "
                            "source_size = ?, source_mtime_ns = ?, source_inode = ? WHERE id = ?",
                            (meta["duration_seconds"], meta["codec"], meta["bit_rate"], meta["width"],
                             source_size, source_mtime_ns, source_inode, existing["id"]),
                        )
                    elif not signature_known:
                        conn.execute(
                            "UPDATE videos SET source_size = ?, source_mtime_ns = ?, source_inode = ? WHERE id = ?",
                            (source_size, source_mtime_ns, source_inode, existing["id"]),
                        )
                    if not get_thumbnail_path(existing["id"]).is_file():
                        generate_thumbnail(existing["id"], path, existing["duration_seconds"])
                else:
                    meta = _probe(path)
                    cursor = conn.execute(
                        "INSERT INTO videos (filepath, title, duration_seconds, codec, bit_rate, width, "
                        "source_size, source_mtime_ns, source_inode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (rel_path, path.stem, meta["duration_seconds"], meta["codec"], meta["bit_rate"],
                         meta["width"], source_size, source_mtime_ns, source_inode),
                    )
                    generate_thumbnail(cursor.lastrowid, path, meta["duration_seconds"])
                    added += 1
                processed += 1
                if processed % COMMIT_EVERY == 0:
                    conn.commit()

        conditions = " OR ".join("(filepath = ? OR filepath LIKE ?)" for _ in valid_prefixes)
        params = [value for prefix in valid_prefixes for value in (prefix, f"{prefix}/%")]
        existing_rows = conn.execute(f"SELECT id, filepath FROM videos WHERE {conditions}", params).fetchall()
        removed = 0
        for row in existing_rows:
            if row["filepath"] not in found_paths:
                marker_ids = [m["id"] for m in conn.execute("SELECT id FROM markers WHERE video_id = ?", (row["id"],)).fetchall()]
                conn.execute("DELETE FROM videos WHERE id = ?", (row["id"],))
                get_thumbnail_path(row["id"]).unlink(missing_ok=True)
                get_transcode_cache_path(row["id"]).unlink(missing_ok=True)
                get_transcode_cache_metadata_path(row["id"]).unlink(missing_ok=True)
                for marker_id in marker_ids:
                    get_marker_frame_path(marker_id).unlink(missing_ok=True)
                removed += 1

        existing_folders = conn.execute("SELECT path FROM media_folders").fetchall()
        kept = [
            row["path"] for row in existing_folders
            if not any(row["path"] == prefix or row["path"].startswith(prefix + "/") for prefix in valid_prefixes)
        ]
        conn.execute("DELETE FROM media_folders")
        conn.executemany("INSERT INTO media_folders (path) VALUES (?)", ((p,) for p in sorted(set(kept) | found_folders)))
        conn.commit()
    finally:
        conn.close()

    return {
        "added": added,
        "removed": removed,
        "unchanged": unchanged,
        "transcoded": 0,
        "transcode_failed": 0,
        "folders": len(found_folders),
    }


def _probe_photo(filepath: Path) -> dict:
    """Fragt Breite/Höhe über ffprobe ab (funktioniert auch für Bilddateien -
    ffprobe behandelt ein Einzelbild wie ein Ein-Frame-Video). Gibt leere
    Werte zurück, falls die Datei nicht lesbar ist (z.B. kaputt/unvollständig)."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                str(filepath),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if streams:
            return {"width": streams[0].get("width"), "height": streams[0].get("height")}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return {"width": None, "height": None}


def _is_too_small(width, height) -> bool:
    """Siehe MIN_PHOTO_DIMENSION - unbekannte Maße (ffprobe konnte die Datei
    nicht lesen) gelten bewusst nicht als 'zu klein', damit im Zweifel lieber
    zu viel als zu wenig katalogisiert wird."""
    return width is not None and height is not None and width < MIN_PHOTO_DIMENSION and height < MIN_PHOTO_DIMENSION


def scan_photos() -> dict:
    """Öffentlicher Einstiegspunkt - siehe _scan_lock oben."""
    with _scan_lock:
        return _scan_photos_impl()


def scan_photo_folders(folders: list[str]) -> dict:
    """Scannt nur explizite Kunden-Fotoordner.

    Anders als der vollständige Scan werden nur Katalogeinträge unter diesen
    Pfaden aktualisiert oder bei gelöschten Dateien entfernt. Dadurch kann ein
    Kunden-Scan weder andere Kunden noch das restliche Archiv beeinflussen.
    """
    with _scan_lock:
        return _scan_photos_impl(folders)


def _scan_photos_impl(folders: list[str] | None = None) -> dict:
    """Läuft durch VIDEOS_DIR (derselbe Archiv-Ordnerbaum wie bei den Videos -
    Foto- und Videomaterial liegt dort gemischt in denselben Projektordnern),
    legt neue Fotos in der DB an und entfernt Einträge für gelöschte Dateien.
    Analog zu _scan_library_impl()."""
    found_paths = set()
    added = 0
    skipped_existing = 0
    ignored_small = 0

    if folders is not None and not [f for f in folders if f.strip().strip("/")]:
        return {"added": 0, "removed": 0, "unchanged": 0, "ignored_small": 0}

    # Bei einem globalen Fotoscan ist ein fehlender Mount ein harter Fehler.
    # Bei einem gezielten Kundenscan bleibt ein noch nicht angelegter Ordner
    # dagegen ein gültiger leerer Zustand und darf nichts löschen.
    if not VIDEOS_DIR.is_dir():
        if folders is None:
            raise RuntimeError("Medien-Mount ist nicht erreichbar; Fotokatalog bleibt unverändert.")
        return {"added": 0, "removed": 0, "unchanged": 0, "ignored_small": 0}

    conn = get_connection()
    try:
        normalized_folders = [f.strip().strip("/") for f in (folders or []) if f.strip().strip("/")]
        scan_roots = [VIDEOS_DIR]
        if folders is not None:
            root = VIDEOS_DIR.resolve()
            scan_roots = []
            for folder in normalized_folders:
                rel = Path(folder)
                if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
                    raise ValueError("Ungültiger Foto-Ordnerpfad.")
                target = (root / rel).resolve()
                if target != root and root not in target.parents:
                    raise ValueError("Ungültiger Foto-Ordnerpfad.")
                if target.is_dir():
                    scan_roots.append(target)

            # Ein konfigurierter Kundenordner, der noch nicht existiert, ist
            # leer, aber kein Anlass, alte Fotos aus dem Katalog zu entfernen.
            if not scan_roots:
                return {"added": 0, "removed": 0, "unchanged": 0, "ignored_small": 0}

        if VIDEOS_DIR.is_dir():
            processed = 0
            for scan_root in scan_roots:
                for path in sorted(scan_root.rglob("*")):
                    if not path.is_file() or path.suffix.lower() not in PHOTO_EXTENSIONS:
                        continue
                    rel_path = str(path.relative_to(VIDEOS_DIR))
                    found_paths.add(rel_path)

                    existing = conn.execute(
                        "SELECT id, width, height FROM photos WHERE filepath = ?", (rel_path,)
                    ).fetchone()
                    if existing:
                        if _is_too_small(existing["width"], existing["height"]):
                            # War vor Einführung von MIN_PHOTO_DIMENSION schon
                            # katalogisiert (z.B. ein Icon/Logo) - jetzt bereinigen.
                            conn.execute("DELETE FROM photos WHERE id = ?", (existing["id"],))
                            get_photo_thumbnail_path(existing["id"]).unlink(missing_ok=True)
                            found_paths.discard(rel_path)
                            ignored_small += 1
                        else:
                            skipped_existing += 1
                            if not get_photo_thumbnail_path(existing["id"]).is_file():
                                generate_photo_thumbnail(existing["id"], path)
                    else:
                        meta = _probe_photo(path)
                        if _is_too_small(meta["width"], meta["height"]):
                            found_paths.discard(rel_path)
                            ignored_small += 1
                        else:
                            try:
                                cursor = conn.execute(
                                    "INSERT INTO photos (filepath, title, width, height) VALUES (?, ?, ?, ?)",
                                    (rel_path, path.stem, meta["width"], meta["height"]),
                                )
                            except sqlite3.IntegrityError:
                                skipped_existing += 1
                            else:
                                generate_photo_thumbnail(cursor.lastrowid, path)
                                added += 1

                    processed += 1
                    if processed % COMMIT_EVERY == 0:
                        conn.commit()

        if normalized_folders:
            conditions = " OR ".join("(filepath = ? OR filepath LIKE ?)" for _ in normalized_folders)
            params = [value for folder in normalized_folders for value in (folder, f"{folder}/%")]
            existing_rows = conn.execute(
                f"SELECT id, filepath FROM photos WHERE {conditions}", params
            ).fetchall()
        else:
            existing_rows = conn.execute("SELECT id, filepath FROM photos").fetchall()
        removed = 0
        for row in existing_rows:
            if row["filepath"] not in found_paths:
                conn.execute("DELETE FROM photos WHERE id = ?", (row["id"],))
                get_photo_thumbnail_path(row["id"]).unlink(missing_ok=True)
                removed += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "added": added,
        "removed": removed,
        "unchanged": skipped_existing,
        "ignored_small": ignored_small,
    }
