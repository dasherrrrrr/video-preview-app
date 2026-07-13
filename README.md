# Video Preview App - Teil 1: Grundgerüst, Login, Nutzerverwaltung

## Lokal starten (zum Testen, ohne Docker)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Beim allerersten Start wird automatisch ein Admin-Account angelegt und das
generierte Passwort **einmalig** in der Konsole ausgegeben - unbedingt notieren,
es wird danach nirgends gespeichert (nur der Hash landet in der Datenbank).

Danach im Browser: http://localhost:8000

## Was schon funktioniert

- Login/Logout mit Session-Cookies
- Passwort-Hashing über `bcrypt`
- Admin-Bereich (`/admin/users`) zum Anlegen weiterer Nutzer, inkl. Admin-Checkbox
- Rechtetrennung: normale Nutzer bekommen 403 auf Admin-Seiten
- SQLite-Datenbank mit dem kompletten Schema für später (users, videos,
  permissions, markers) - videos/permissions/markers werden erst in Teil 2 befüllt
- Dunkles UI ohne Frameworks (reines CSS)

## Was als Nächstes kommt (Teil 2)

- Verzeichnis mit den Videodateien einlesen (Katalog-Scan + `ffprobe` für Metadaten)
- Videos den Nutzern über die Admin-Oberfläche zuweisen (permissions-Tabelle befüllen)
- Video-Player im Dashboard mit Streaming-Endpoint (Direct Play + VAAPI-Transcode
  für H.265, siehe die Docker/VAAPI-Tests, die wir schon gemacht haben)
- Marker setzen und anzeigen

## Projektstruktur

```
app/
  main.py            - FastAPI-App, Middleware, Startup-Logik
  database.py         - SQLite-Verbindung + Schema
  auth.py              - Passwort-Hashing, Session-Handling, Login-Dependencies
  routers/
    auth_routes.py     - /login, /logout
    dashboard.py        - / (Video-Übersicht, aktuell Platzhalter)
    admin.py             - /admin/users (Nutzerverwaltung)
  templates/            - Jinja2-HTML-Templates
  static/css/style.css   - Styling
data/                    - hier landet app.db (wird automatisch angelegt)
```
