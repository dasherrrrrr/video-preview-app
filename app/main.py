import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .auth import NotAuthenticated, hash_password
from .database import get_db, init_db
from .routers import admin, auth_routes, comments, dashboard, markers, streaming

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Video Preview App")

# SESSION_SECRET per Umgebungsvariable setzen (z.B. im Docker-Compose/Unraid-Template).
# Ohne gesetzte Variable wird bei jedem Neustart ein neuer Zufalls-Key erzeugt -
# das funktioniert, meldet aber alle bestehenden Sessions ab (kein Problem für
# den lokalen Betrieb, nur gut zu wissen).
SECRET_KEY = os.environ.get("SESSION_SECRET", secrets.token_urlsafe(32))
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(auth_routes.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(streaming.router)
app.include_router(markers.router)
app.include_router(comments.router)


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    return RedirectResponse(url="/login", status_code=303)


@app.on_event("startup")
def on_startup():
    init_db()
    with get_db() as conn:
        user_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if user_count == 0:
            password = secrets.token_urlsafe(12)
            conn.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
                ("admin", hash_password(password)),
            )
            print("=" * 60)
            print("Erster Start: Admin-Account wurde angelegt.")
            print("  Benutzername: admin")
            print(f"  Passwort:     {password}")
            print("Bitte jetzt notieren - wird nur dieses eine Mal angezeigt!")
            print("=" * 60)
