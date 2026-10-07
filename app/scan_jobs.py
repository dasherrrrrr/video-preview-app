"""Background state for the potentially expensive video-library scan.

The scan walks the complete media mount and may run for several minutes. Keeping
its state here lets the HTTP endpoint acknowledge the request immediately while
the admin UI polls for completion instead of hitting a reverse-proxy timeout.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import threading
import uuid

from .catalog import scan_library, scan_photos, scan_photo_folders


_lock = threading.Lock()
_state: dict = {
    "status": "idle",
    "job_id": None,
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": None,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_scan_status(job_id: str | None = None) -> dict:
    with _lock:
        state = deepcopy(_state)
    if job_id and state["job_id"] and state["job_id"] != job_id:
        # A newer scan replaced the requested job. Returning the current state
        # is more useful than a 404 for a browser that was left open overnight.
        state["requested_job_id"] = job_id
    return state


def _run_scan(job_id: str, folders: list[str] | None = None) -> None:
    try:
        result = scan_library(folders)
        # Bei einem gezielten Video-Scan werden Fotos separat über den
        # kundenspezifischen Foto-Scan aktualisiert.
        photo_result = scan_photos() if folders is None else scan_photo_folders(folders)
        result["photos_added"] = photo_result["added"]
        result["photos_removed"] = photo_result["removed"]
        result["photos_unchanged"] = photo_result["unchanged"]
        result["photos_ignored_small"] = photo_result["ignored_small"]
    except Exception as exc:  # pragma: no cover - defensive production guard
        with _lock:
            if _state["job_id"] != job_id:
                return
            _state.update(
                {
                    "status": "failed",
                    "finished_at": _now(),
                    "error": str(exc) or exc.__class__.__name__,
                }
            )
        return

    with _lock:
        if _state["job_id"] != job_id:
            return
        _state.update(
            {
                "status": "completed",
                "finished_at": _now(),
                "result": result,
                "error": None,
            }
        )


def start_scan(folders: list[str] | None = None) -> dict:
    with _lock:
        if _state["status"] == "running":
            return deepcopy(_state)
        job_id = uuid.uuid4().hex
        _state.clear()
        _state.update(
            {
                "status": "running",
                "job_id": job_id,
                "started_at": _now(),
                "finished_at": None,
                "result": None,
                "error": None,
            }
        )
    threading.Thread(target=_run_scan, args=(job_id, folders), name="video-library-scan", daemon=True).start()
    return get_scan_status(job_id)
