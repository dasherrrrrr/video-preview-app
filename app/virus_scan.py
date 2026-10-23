"""Fail-closed malware scan for customer uploads."""

from __future__ import annotations

import os
import socket
import struct
from pathlib import Path

from fastapi import HTTPException

CLAMAV_HOST = os.environ.get("CLAMAV_HOST", "clamav")
CLAMAV_PORT = int(os.environ.get("CLAMAV_PORT", "3310"))
CLAMAV_TIMEOUT_SECONDS = int(os.environ.get("CLAMAV_TIMEOUT_SECONDS", "180"))


def scan_for_malware(path: Path) -> None:
    """Scan via clamd's INSTREAM protocol, rejecting scanner outages too."""
    try:
        with socket.create_connection((CLAMAV_HOST, CLAMAV_PORT), timeout=CLAMAV_TIMEOUT_SECONDS) as client:
            client.settimeout(CLAMAV_TIMEOUT_SECONDS)
            client.sendall(b"zINSTREAM\0")
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    client.sendall(struct.pack("!I", len(chunk)))
                    client.sendall(chunk)
            client.sendall(struct.pack("!I", 0))
            response = client.recv(4096).decode("utf-8", "replace").strip()
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail="Der Sicherheits-Scanner ist gerade nicht erreichbar. Der Upload wurde nicht gespeichert; bitte später erneut versuchen.",
        ) from exc
    if response.endswith("OK"):
        return
    if "FOUND" in response:
        raise HTTPException(status_code=400, detail="Die Datei wurde vom Sicherheits-Scanner abgewiesen.")
    raise HTTPException(
        status_code=400,
        detail="Die Datei konnte nicht vollständig sicher geprüft werden und wurde daher abgewiesen.",
    )
