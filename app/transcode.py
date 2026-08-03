"""
On-Demand-Transcoding für Codecs, die die meisten Browser nicht direkt abspielen
(v.a. HEVC/H.265 der DJI-Drohne). Nutzt VAAPI für Hardware-Decode+Encode auf der
Intel-GPU. Ergebnis landet im data/transcoded/-Cache (Teil des data-Volumes) -
ein Video wird also nur beim allerersten Aufruf transkodiert, danach direkt
aus dem Cache bedient.
"""

import subprocess
from pathlib import Path

from .database import DB_PATH

# Codecs, die praktisch jeder Browser im <video>-Tag direkt abspielen kann -
# alles andere (v.a. hevc/h265) wird vor der Auslieferung transkodiert.
BROWSER_COMPATIBLE_CODECS = {"h264", "vp8", "vp9", "av1"}

TRANSCODE_CACHE_DIR = DB_PATH.parent / "transcoded"

# Der Ziel-Pfad im Container ist immer fix - docker-compose.yml mappt die
# tatsächliche GPU des Hosts (über VAAPI_DEVICE in .env) immer auf diesen Node.
VAAPI_DEVICE = "/dev/dri/renderD128"


def needs_transcode(codec: str | None) -> bool:
    return bool(codec) and codec.lower() not in BROWSER_COMPATIBLE_CODECS


def get_cache_path(video_id: int) -> Path:
    return TRANSCODE_CACHE_DIR / f"{video_id}.mp4"


def ensure_transcoded(video_id: int, source_path: Path) -> Path:
    """Gibt den Pfad der H.264-Version zurück, transkodiert bei Bedarf zuerst.
    Blockiert den aufrufenden Thread (FastAPI führt sync-Routen im Threadpool
    aus, daher ist das für dieses Preview-App-Nutzungsmuster ok)."""
    TRANSCODE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = get_cache_path(video_id)
    if dest_path.is_file():
        return dest_path

    # In eine .tmp-Datei schreiben und erst am Ende umbenennen, damit ein
    # abgebrochener Transcode nie eine halbfertige Datei als "fertig" ausgibt.
    tmp_path = dest_path.with_suffix(".tmp.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-hwaccel", "vaapi",
        "-vaapi_device", VAAPI_DEVICE,
        "-hwaccel_output_format", "vaapi",
        "-i", str(source_path),
        # DJI-Drohnenclips liegen teils als 10-Bit HEVC (Main10/P010) vor -
        # h264_vaapi kann aber nur 8-Bit NV12 encodieren ("No usable encoding
        # profile found" sonst). scale_vaapi konvertiert das auf der GPU,
        # ist für bereits-8-Bit-Quellen ein No-Op.
        "-vf", "scale_vaapi=format=nv12",
        "-c:v", "h264_vaapi",
        "-qp", "24",  # konstante Qualität statt unbegrenzter Bitrate (sonst oft größer als Quelle)
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(tmp_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=1800)
    except subprocess.CalledProcessError as exc:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg-Transcode fehlgeschlagen: {exc.stderr}") from exc
    except subprocess.TimeoutExpired as exc:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg-Transcode hat das Zeitlimit überschritten.") from exc

    tmp_path.rename(dest_path)
    return dest_path
