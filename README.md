# Video Preview App

Werkzeug für Videovorschau und -abnahme: Admins pflegen einen Videokatalog
(HEVC-Drohnenclips o.ä. werden automatisch nach H.264/720p transkodiert),
externe Kunden sehen ihre freigegebenen Videos, setzen Marker und schreiben
Kommentare - über eine Token-basierte REST-API, die von einem beliebigen
Frontend (eigenes Kundenportal, No-Code-Tool, o.ä.) angesprochen werden kann.
Die App selbst hat kein eigenes Kunden-Login mehr; nur Admins/Bearbeiter
melden sich direkt an, um den Katalog und die Kundenzuweisungen zu pflegen.

## Lokal starten (zum Testen, ohne Docker)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Beim allerersten Start wird automatisch ein Admin-Account angelegt und das
generierte Passwort **einmalig** in der Konsole ausgegeben - unbedingt notieren,
es wird danach nirgends gespeichert (nur der Hash landet in der Datenbank).

Danach im Browser: http://localhost:8000 (leitet zu `/login` bzw. nach Login
zu `/admin/videos` weiter).

## Deployment per Docker (Server-Update via git pull)

```bash
git clone https://github.com/dasherrrrrr/video-preview-app.git
cd video-preview-app
cp .env.example .env   # HOST_PORT, VIDEOS_HOST_PATH, SESSION_SECRET, VAAPI_DEVICE, SMTP_* anpassen
docker compose up -d --build
```

Updates danach einfach mit `./update.sh` (macht `git pull` + Rebuild). `.env` liegt
nicht in Git, server-spezifische Werte bleiben also bei jedem Pull unangetastet.

Läuft die App hinter einem Reverse-Proxy (nginx, Nginx Proxy Manager, Traefik, ...),
auf einen ausreichenden `proxy_read_timeout` achten - der erste Aufruf eines neuen,
noch nicht gecachten Videos kann je nach Länge etwas dauern (siehe Vortranskodierung
unten, die genau das vermeiden soll).

## Was schon funktioniert

### Admin-Bereich (Session-Login)

- Login/Logout mit Session-Cookies, Passwort-Hashing über `bcrypt` - **nur für
  Admin-/Bearbeiter-Accounts**, normale Kunden haben hier keinen Zugang mehr
- Nutzerverwaltung (`/admin/users`): Kunden anlegen, Admin-Rechte vergeben,
  Bearbeiter pro Kunde zuweisen, API-Token pro Kunde erzeugen/widerrufen
- Katalog-Scan (`/admin/videos`, auch per API auslösbar): liest `VIDEOS_DIR`
  rekursiv ein, holt Dauer/Codec per `ffprobe`, hält die `videos`-Tabelle mit
  dem Dateisystem synchron (neue Dateien aufnehmen, gelöschte entfernen -
  zugehörige Kunden-Zuweisungen werden dabei automatisch mit entfernt) und
  **transkodiert neue Videos direkt vor**, statt erst beim ersten Kundenaufruf
- Thumbnails: automatisch per `ffmpeg`-Frame beim Scan erzeugt, Admins können
  pro Video ein eigenes PNG/JPG hochladen - überschreibt das automatische Bild
  dauerhaft, auch über künftige Scans hinweg
- Admin kann Video-Titel umbenennen
- Video-Zuweisung (`/admin/users/<id>/permissions`): befüllt die
  `permissions`-Tabelle, gruppiert nach Ordner (z.B. Datumsordner der
  DJI-Clips) mit einer "alle in diesem Ordner"-Checkbox
- Video-Detail-Ansicht für Admins/Bearbeiter (`/admin/videos/<id>`): Video
  ansehen, Marker der Kunden einsehen, Kommentare lesen und beantworten
- Admin-Übersicht über alle Marker aller Kunden (`/admin/markers`)
- Eigenes Passwort ändern, Kontaktdaten pflegen (`/account`)
- Mailversand: schreibt ein Kunde (per API) einen Kommentar, bekommt sein
  zugewiesener Bearbeiter automatisch eine Mail. Ohne konfiguriertes SMTP
  bleibt der Versand einfach aus, kein Fehler
- Admin-Einstellungsseite (`/admin/settings`): SMTP-Zugangsdaten und die
  öffentliche App-URL bequem im Web-UI pflegen, inkl. "Test-Mail senden"
- Branding: App-Name, Logo und Favicon lassen sich hochladen, landen in
  `data/branding/` und werden über `/branding/logo` bzw. `/branding/favicon`
  ausgeliefert - auch auf der Login-Seite sichtbar
- Dunkles UI ohne Frameworks (reines CSS)

### Video-Transcoding

- VAAPI-Hardware-Transcoding (Intel-GPU): Videos, deren Codec ein Browser
  nicht direkt abspielen kann (v.a. HEVC/H.265, inkl. 10-Bit/Main10-Quellen),
  werden nach H.264 transkodiert und dabei auf **720p** herunterskaliert -
  deutlich schnellerer Transcode und kleinere Cache-Dateien als bei
  Kamera-Originalauflösung. Ergebnis landet in `data/transcoded/` und wird
  danach direkt aus dem Cache bedient
- Läuft sowohl automatisch beim Katalog-Scan (siehe oben) als auch on-demand
  beim ersten Stream-Aufruf eines Videos, das noch nicht im Cache liegt

### Externe REST-API (Token-Auth, für beliebige Frontends)

Zwei Auth-Ebenen, beide über `Authorization: Bearer <token>` (Stream-/
Thumbnail-Endpunkte akzeptieren den Token zusätzlich per `?t=`-Query-Parameter,
weil `<video src>`/`<img src>` keine Header setzen können):

- **Kunden-Token** (`/api/*`) - scoped auf die einem Kunden zugewiesenen
  Videos: Videos auflisten, Details inkl. Marker/Kommentare, Streaming
  (Range-Requests), Thumbnails, Marker/Kommentare anlegen und löschen
- **Admin-Token** (`/api/admin/*`) - für die Verwaltung aus einem externen
  System heraus: Kunden anlegen, Kunden-Token erzeugen/widerrufen,
  Video-Katalog auflisten, Zuweisung pro Kunde lesen/setzen,
  Katalog-Rescan auslösen

CORS ist offen (inkl. Preflight für POST/DELETE), da die Auth über den
Bearer-Token läuft statt über Cookies - ein fremdes Origin kommt ohne
gültigen Token nicht rein.

## Projektstruktur

```
app/
  main.py                  - FastAPI-App, Middleware (Sessions, CORS), Startup-Logik
  database.py               - SQLite-Verbindung + Schema
  auth.py                    - Passwort-Hashing, Session-Handling, Login-Dependencies
  api_auth.py                 - Token-Auth-Dependencies (Kunde/Admin) für die REST-API
  customers.py                  - Gemeinsame Kunden-Verwaltungslogik (Admin-UI + Admin-API)
  catalog.py                     - Katalog-Scan (ffprobe) + Vortranskodierung
  media.py                        - Gemeinsame Berechtigungsprüfung + Range-Streaming
  transcode.py                     - VAAPI-Transcoding (720p) + Cache
  mailer.py                         - SMTP-Mailversand
  settings.py                        - Key-Value-Einstellungen (DB, mit .env-Fallback)
  branding.py                         - Logo/Favicon-Uploads
  templates_env.py                     - gemeinsame Jinja2Templates-Instanz für alle Router
  routers/
    auth_routes.py                     - /login, /logout, / (Redirect je nach Login-Status)
    admin.py                            - /admin/users, /admin/videos, /admin/videos/<id>
    streaming.py                         - /thumbnail/<id>, /stream/<id> (Session-Login)
    api.py                                 - /api/* (Kunden-Token: Videos, Marker, Kommentare)
    api_admin.py                            - /api/admin/* (Admin-Token: Verwaltung)
    account.py                               - /account (eigenes Passwort/Kontaktdaten)
    settings.py                               - /admin/settings
    branding.py                                - /branding/<logo|favicon>
  templates/                                  - Jinja2-HTML-Templates
  static/css/style.css                          - Styling
data/                                             - app.db, transcoded/, thumbnails/, branding/ (automatisch angelegt)
```
