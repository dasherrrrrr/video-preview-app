import bcrypt
from fastapi import Request, HTTPException, status

from .database import get_db


class NotAuthenticated(Exception):
    """Wird geworfen, wenn eine Route Login braucht, aber keiner eingeloggt ist.
    In main.py gibt es dafür einen Exception-Handler, der auf /login umleitet."""
    pass


def hash_password(password: str) -> str:
    # bcrypt begrenzt Passwörter technisch auf 72 Bytes - für normale Passwörter
    # kein praktisches Problem, daher hier bewusst kein zusätzliches Handling.
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def get_user_by_username(username: str):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()


def get_user_by_id(user_id: int):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def get_current_user(request: Request):
    """Gibt den eingeloggten User zurück oder None. Wirft keinen Fehler -
    für Stellen, wo Login optional ist (z.B. Login-Seite selbst)."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(user_id)


def require_login(request: Request):
    """FastAPI-Dependency: In eine Route einbauen mit
    user = Depends(require_login) - wirft NotAuthenticated wenn kein Login."""
    user = get_current_user(request)
    if not user:
        raise NotAuthenticated()
    return user


def require_admin(request: Request):
    """Wie require_login, aber zusätzlich muss is_admin gesetzt sein."""
    user = require_login(request)
    if not user["is_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Diese Seite ist nur für Admins zugänglich.",
        )
    return user
