"""
Sehr bewusst KEIN ORM (kein SQLAlchemy) - für dieses Projekt reicht rohes SQL
über das eingebaute sqlite3-Modul völlig aus und ist einfacher nachzuvollziehen,
wenn man noch keine Web-Vorkenntnisse hat. Jede Funktion hier ist eine einzelne,
nachvollziehbare Datenbank-Operation.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "app.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    # row_factory sorgt dafür, dass wir auf Spalten per Name zugreifen können
    # (z.B. row["username"]) statt nur per Index (row[0])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    """
    Nutzung: with get_db() as conn: conn.execute(...)
    Committed automatisch am Ende und schließt die Verbindung sauber.
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                duration_seconds REAL,
                codec TEXT,
                added_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Welcher User welches Video sehen darf (n:m-Beziehung)
            CREATE TABLE IF NOT EXISTS permissions (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, video_id)
            );

            -- Marker, die ein User in einem Video an einer bestimmten Stelle setzt
            CREATE TABLE IF NOT EXISTS markers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                timestamp_seconds REAL NOT NULL,
                label TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Kommentare zu einem Video, sichtbar für alle Nutzer mit Zugriff
            -- auf das Video (nicht privat wie Marker) - für Status-Austausch
            -- zwischen Admin und Nutzern.
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        _migrate_add_columns(conn, "users", {"email": "TEXT", "phone": "TEXT"})


def _migrate_add_columns(conn: sqlite3.Connection, table: str, columns: dict) -> None:
    """Ergänzt fehlende Spalten in einer bestehenden Tabelle. SQLite kennt kein
    ALTER TABLE ADD COLUMN IF NOT EXISTS, daher wird das über PRAGMA table_info
    selbst geprüft - so bleibt eine schon laufende Datenbank (mit Bestandsdaten)
    beim Deploy einer neuen Version kompatibel, ohne dass jemand manuell migrieren muss."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for column, sql_type in columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
