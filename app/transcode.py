"""
Vorschau-Transcoding: jedes Video, das einem Kunden zugewiesen wird, wird
auf ein einheitliches Vorschauformat gebracht (1080p, ~12 Mbit/s H.264) -
unabhängig vom Quellcodec/-bitrate. Das sorgt für gleichmäßiges, planbares
Abspielverhalten auf allen Geräten/Verbindungen und ist einfacher als eine
Fallunterscheidung "brauchts überhaupt". Nutzt VAAPI für Hardware-Decode+
Encode auf der Intel-GPU. Ergebnis landet im data/transcoded/-Cache (Teil
des data-Volumes) - ein Video wird also nur beim allerersten Aufruf
transkodiert, danach direkt aus dem Cache bedient.
"""

import subprocess
from pathlib import Path

from .database import DB_PATH

TRANSCODE_CACHE_DIR = DB_PATH.parent / "transcoded"

# Der Ziel-Pfad im Container ist immer fix - docker-compose.yml mappt die
# tatsächliche GPU des Hosts (über VAAPI_DEVICE in .env) immer auf diesen Node.
VAAPI_DEVICE = "/dev/dri/renderD128"

TARGET_MAX_BIT_RATE = "12M"
TARGET_BUFSIZE = "24M"
TARGET_QUALITY = 23  # ICQ: niedriger = bessere Qualität, ~18-28 ist der übliche Bereich


def get_cache_path(video_id: int) -> Path:
    return TRANSCODE_CACHE_DIR / f"{video_id}.mp4"


def ensure_transcoded(video_id: int, source_path: Path) -> Path:
    """Gibt den Pfad der 1080p/12-Mbit-Vorschauversion zurück, transkodiert bei
    Bedarf zuerst. Blockiert den aufrufenden Thread (FastAPI führt sync-Routen
    im Threadpool aus, daher ist das für dieses Preview-App-Nutzungsmuster
    ok)."""
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
        # Nur den ersten Video- und Audio-Stream übernehmen. Ohne das würde
        # z.B. ein Timecode-Datenstream (üblich bei professionellen Kameras,
        # Codec "tmcd") unverändert mitkopiert - der bekommt beim Neu-Muxen
        # keine passende neue Zeitbasis und macht dann die Dauer/Bitrate des
        # gesamten Containers kaputt (führte zu "Duration: N/A" und einem
        # Video, das im Browser gar nicht oder nur schwarz mit Ton abspielt).
        "-map", "0:v:0",
        "-map", "0:a:0",
        # Runterskalieren auf 1080p (Breite automatisch, gerade Zahl via -2) -
        # für eine Vorschau reicht das, transkodiert spürbar schneller und
        # ergibt kleinere Dateien als die Kamera-Originalauflösung (meist 4K).
        # format=nv12 löst nebenbei auch 10-Bit-HEVC-Quellen (DJI Main10/P010)
        # auf 8-Bit auf - h264_vaapi kann sonst nur 8-Bit encodieren ("No
        # usable encoding profile found").
        "-vf", "scale_vaapi=w=-2:h=1080:format=nv12",
        "-c:v", "h264_vaapi",
        "-profile:v", "main",  # breit kompatibles Profil (iOS/Safari-Hardwaredecode)
        "-level", "4.1",  # deckt 1080p bei dieser Bitrate/Framerate sicher ab
        # Qualitätsbasierte Ratensteuerung (ICQ) statt fester Ziel-Bitrate:
        # bei fester Bitrate (-b:v) sichtbares Flimmern/Artefakte in
        # bewegungsreichen/detailreichen Szenen, weil der Hardware-Encoder
        # die Qualität dafür pro Frame runterregeln musste, um die Ziel-
        # Bitrate zu halten. ICQ hält stattdessen die Qualität konstant und
        # lässt die Bitrate je nach Szene schwanken - -maxrate/-bufsize
        # bleiben als Deckel, damit sehr komplexe Szenen nicht ausufern.
        "-rc_mode", "ICQ",
        "-global_quality", str(TARGET_QUALITY),
        "-maxrate", TARGET_MAX_BIT_RATE,
        "-bufsize", TARGET_BUFSIZE,
        # Festes, kurzes Keyframe-Intervall (alle 2s bei 24fps) statt
        # VAAPI-Standard (oft deutlich länger) - sorgt für gleichmäßigeres
        # Rebuffering-Verhalten und schnelleres Seeking/Starten auf Mobilgeräten.
        "-g", "48",
        "-keyint_min", "48",
        "-sc_threshold", "0",
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
