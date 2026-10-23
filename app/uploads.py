"""Hardened customer uploads with dedicated photo/video targets.

Files are written into a private temporary file, structure-validated and
malware-scanned before being atomically moved into the customer archive.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .virus_scan import scan_for_malware

VIDEOS_RW_DIR = Path(os.environ.get("VIDEOS_RW_DIR", "/videos-rw"))
DEFAULT_QUOTA_BYTES = 5 * 1024 * 1024 * 1024
MAX_UPLOAD_FILE_BYTES = int(os.environ.get("MAX_UPLOAD_FILE_BYTES", str(25 * 1024**3)))

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".webm", ".mkv"}
ALLOWED_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS


def _safe_relative_folder(value: str) -> Path:
    rel = Path((value or "").strip().strip("/"))
    if not str(rel) or rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise HTTPException(status_code=500, detail="Ungültiger Upload-Zielordner konfiguriert.")
    return rel


def _safe_component(value: str, fallback: str, maximum: int = 80) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = re.sub(r"[\\/\x00-\x1f]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß ._-]", "_", normalized).strip(" ._")
    return normalized[:maximum].strip() or fallback


def _sanitize_filename(filename: str) -> str:
    name = _safe_component(Path(unicodedata.normalize("NFKC", filename or "")).name, "datei", 180)
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Dieser Dateityp ist nicht erlaubt.")
    return name


def _configured_folder(user, area: str) -> str:
    if area not in {"photo", "video"}:
        raise HTTPException(status_code=400, detail="Ungültiger Upload-Bereich.")
    column = "photo_upload_folder" if area == "photo" else "video_upload_folder"
    folder = user[column] if column in user.keys() else None
    if not folder:
        raise HTTPException(status_code=403, detail="Für diesen Bereich wurde noch kein konkreter Upload-Zielordner freigegeben.")
    return str(_safe_relative_folder(folder)).replace("\\", "/")


def _customer_upload_root(folder: str) -> Path:
    root = VIDEOS_RW_DIR.resolve()
    target = (root / _safe_relative_folder(folder) / "Kundenuploads").resolve()
    if root not in target.parents:
        raise HTTPException(status_code=500, detail="Ungültiger Upload-Zielordner konfiguriert.")
    return target


def _usage_roots(folders: list[str]) -> list[Path]:
    result: list[Path] = []
    for folder in folders:
        if folder:
            root = _customer_upload_root(folder)
            if root not in result:
                result.append(root)
    return result


def get_quota_usage(folders: list[str], quota_bytes: int | None = None) -> dict:
    quota = quota_bytes if quota_bytes and quota_bytes > 0 else DEFAULT_QUOTA_BYTES
    files: list[dict] = []
    total = 0
    for root in _usage_roots(folders):
        if not root.is_dir():
            continue
        for entry in sorted(root.rglob("*")):
            if entry.is_file() and not entry.name.endswith(".partial"):
                size = entry.stat().st_size
                total += size
                files.append({"filename": str(entry.relative_to(root)).replace("\\", "/"), "size_bytes": size})
    return {"files": files, "used_bytes": total, "quota_bytes": quota, "remaining_bytes": max(0, quota - total)}


def _matches_signature(path: Path, extension: str) -> bool:
    with path.open("rb") as source:
        header = source.read(64)
    if extension in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    if extension == ".webp":
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    if extension in {".heic", ".heif", ".mp4", ".mov", ".m4v"}:
        return len(header) >= 12 and header[4:8] == b"ftyp"
    if extension in {".webm", ".mkv"}:
        return header.startswith(b"\x1a\x45\xdf\xa3")
    if extension == ".avi":
        return header.startswith(b"RIFF") and header[8:12] == b"AVI "
    return False


def _validate_media(path: Path, extension: str, area: str) -> None:
    if extension not in (PHOTO_EXTENSIONS if area == "photo" else VIDEO_EXTENSIONS):
        raise HTTPException(status_code=400, detail=f"In diesem Bereich sind nur {'Fotos' if area == 'photo' else 'Videos'} erlaubt.")
    if not _matches_signature(path, extension):
        raise HTTPException(status_code=400, detail="Dateiendung und tatsächlicher Dateityp stimmen nicht überein.")
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=45, check=True,
        )
        streams = json.loads(result.stdout).get("streams", [])
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Die Datei ist beschädigt oder kein gültiges Medienformat.") from exc
    if not any(stream.get("codec_type") == "video" for stream in streams):
        raise HTTPException(status_code=400, detail="Die Datei enthält keinen gültigen Bild- oder Videostream.")


async def _write_temporary(upload: UploadFile, destination_dir: Path, remaining_bytes: int) -> tuple[Path, int]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".upload-", suffix=".partial", dir=destination_dir)
    temporary = Path(name)
    total = 0
    try:
        with os.fdopen(fd, "wb") as target:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_FILE_BYTES:
                    raise HTTPException(status_code=413, detail="Die Datei überschreitet die zulässige Einzeldateigröße.")
                if total > remaining_bytes:
                    raise HTTPException(status_code=400, detail="Das Speicherlimit würde überschritten.")
                target.write(chunk)
        if not total:
            raise HTTPException(status_code=400, detail="Datei ist leer.")
        return temporary, total
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _next_target(directory: Path, filename: str) -> Path:
    target = directory / filename
    index = 2
    while target.exists():
        target = directory / f"{Path(filename).stem}_{index}{Path(filename).suffix}"
        index += 1
    return target


async def save_customer_upload(user, area: str, upload_name: str, upload: UploadFile) -> dict:
    folder = _configured_folder(user, area)
    safe_name = _sanitize_filename(upload.filename or "datei")
    batch = f"{datetime.now().strftime('%Y.%m.%d')} - {_safe_component(user['username'], 'Kunde')} - {_safe_component(upload_name, 'Upload')}"
    batch_dir = _customer_upload_root(folder) / batch
    folders = [user[key] for key in ("photo_upload_folder", "video_upload_folder") if key in user.keys() and user[key]]
    usage = get_quota_usage(folders, user["upload_quota_bytes"])
    temporary, size = await _write_temporary(upload, batch_dir, usage["remaining_bytes"])
    try:
        _validate_media(temporary, Path(safe_name).suffix.lower(), area)
        scan_for_malware(temporary)
        target = _next_target(batch_dir, safe_name)
        shutil.move(str(temporary), target)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "filename": target.name,
        "size_bytes": size,
        "filepath": str(target.relative_to(VIDEOS_RW_DIR)).replace("\\", "/"),
        "batch": batch,
        "folder": folder,
    }


def delete_upload(folders: list[str], filename: str) -> None:
    rel = Path(filename)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise HTTPException(status_code=400, detail="Ungültiger Dateiname.")
    for root in _usage_roots(folders):
        target = (root / rel).resolve()
        if target.is_file() and root in target.parents:
            target.unlink()
            return
    raise HTTPException(status_code=404, detail="Datei nicht gefunden.")
