"""
Eine gemeinsame Jinja2Templates-Instanz für alle Router (statt einer eigenen
pro Datei) - nötig, damit get_setting als Template-Funktion überall verfügbar
ist (z.B. für Branding/Favicon in base.html, ohne dass jede Route das einzeln
in den Kontext packen muss).
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from .settings import get_setting

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["get_setting"] = get_setting
