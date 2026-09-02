"""Datei-Upload für Kunden (Bilder/kleine Videos) mit Kontingent pro Kunde.

Nutzt einen SEPARATEN, beschreibbaren Mount (VIDEOS_RW_DIR) auf denselben
Host-Pfad wie VIDEOS_DIR (siehe docker-compose.yml) - der normale VIDEOS_DIR-
Mount bleibt read-only, damit Lese-/Streaming-/Scan-Code nie versehentlich das
Quellmaterial verändern kann. Nur dieses Modul schreibt, und ausschließlich
innerhalb von "<upload_folder>/upload/" eines Kunden.

Der genutzte Speicher wird nicht separat mitgezählt (keine eigene Spalte,
kein Nachziehen nötig), sondern bei jedem Zugriff durch Aufsummieren der
tatsächlich im Upload-Ordner liegenden Dateien berechnet - so kann die Zahl
nie mit dem echten Dateisystem auseinanderlaufen. Das Kontingent selbst
(die Obergrenze) ist dagegen pro Kunde in users.upload_quota_bytes
gespeichert (siehe customers.set_upload_quota) und wird von den Aufrufern
hier durchgereicht - Standardwert DEFAULT_QUOTA_BYTES, falls nichts gesetzt."""

import os
import re
import unicodedata
from pathlib import Path

from fastapi import HTTPException

VIDEOS_RW_DIR = Path(os.environ.get("VIDEOS_RW_DIR", "/videos-rw"))

DEFAULT_QUOTA_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB, falls kein individuelles Kontingent gesetzt ist

ALLOWED_EXTENSIONS = {
    # Bilder
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif",
    # Videos
    ".mp4", ".mov", ".m4v", ".avi", ".webm", ".mkv",
}


def _sanitize_filename(filename: str) -> str:
    """Macht einen vom Kunden übermittelten Dateinamen sicher: keine Pfad-
    Anteile, keine Sonderzeichen, die auf manchen Dateisystemen Probleme
    machen. Wirft bei leerem/unbrauchbarem Ergebnis oder nicht erlaubter
    Endung eine HTTPException."""
    name = unicodedata.normalize("NFKC", filename or "")
    name = Path(name).name  # nur den letzten Pfad-Teil behalten (kein "../")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._") or "datei"
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Dateityp '{ext}' nicht erlaubt. Erlaubt: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    return name


def get_upload_dir(upload_folder: str, create: bool = False) -> Path:
    """Liefert den Upload-Ordner eines Kunden (<VIDEOS_RW_DIR>/<upload_folder>/upload).
    upload_folder kommt aus der DB (admin-gesetzt, siehe customers.set_upload_folder),
    nicht vom Kunden selbst - trotzdem wird auch hier gegen Pfad-Traversal
    abgesichert, statt dem Admin-Wert blind zu vertrauen."""
    base = (VIDEOS_RW_DIR / upload_folder / "upload").resolve()
    try:
        base.relative_to(VIDEOS_RW_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=500, detail="Ungültiger Upload-Ordner konfiguriert.")
    if create:
        base.mkdir(parents=True, exist_ok=True)
    return base


def get_quota_usage(upload_folder: str, quota_bytes: int | None = None) -> dict:
    quota = quota_bytes if quota_bytes and quota_bytes > 0 else DEFAULT_QUOTA_BYTES
    upload_dir = get_upload_dir(upload_folder)
    files = []
    total = 0
    if upload_dir.is_dir():
        for entry in sorted(upload_dir.iterdir()):
            if entry.is_file():
                size = entry.stat().st_size
                total += size
                files.append({"filename": entry.name, "size_bytes": size})
    return {
        "files": files,
        "used_bytes": total,
        "quota_bytes": quota,
        "remaining_bytes": max(0, quota - total),
    }


def save_upload(upload_folder: str, filename: str, content: bytes, quota_bytes: int | None = None) -> dict:
    safe_name = _sanitize_filename(filename)
    usage = get_quota_usage(upload_folder, quota_bytes)
    if usage["used_bytes"] + len(content) > usage["quota_bytes"]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Kontingent überschritten: {usage['remaining_bytes'] / (1024*1024):.0f} MB "
                f"frei, Datei ist {len(content) / (1024*1024):.0f} MB groß."
            ),
        )
    upload_dir = get_upload_dir(upload_folder, create=True)
    target = upload_dir / safe_name
    if target.exists():
        stem, ext = target.stem, target.suffix
        i = 2
        while target.exists():
            target = upload_dir / f"{stem}_{i}{ext}"
            i += 1
    target.write_bytes(content)
    return {"filename": target.name, "size_bytes": len(content)}


def delete_upload(upload_folder: str, filename: str) -> None:
    safe_name = _sanitize_filename(filename)
    upload_dir = get_upload_dir(upload_folder)
    target = upload_dir / safe_name
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden.")
    target.unlink()
