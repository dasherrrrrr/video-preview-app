# Video Preview App

## Lokal starten (zum Testen, ohne Docker)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Beim allerersten Start wird automatisch ein Admin-Account angelegt und das
generierte Passwort **einmalig** in der Konsole ausgegeben - unbedingt notieren,
es wird danach nirgends gespeichert (nur der Hash landet in der Datenbank).

Danach im Browser: http://localhost:8000

## Deployment per Docker (Server-Update via git pull)

```bash
git clone https://github.com/dasherrrrrr/video-preview-app.git
cd video-preview-app
cp .env.example .env   # HOST_PORT, VIDEOS_HOST_PATH, SESSION_SECRET, VAAPI_DEVICE, SMTP_* anpassen
docker compose up -d --build
```

Updates danach einfach mit `./update.sh` (macht `git pull` + Rebuild). `.env` liegt
nicht in Git, server-spezifische Werte bleiben also bei jedem Pull unangetastet.

## Was schon funktioniert (Teil 1 + Teil 2)

- Login/Logout mit Session-Cookies, Passwort-Hashing über `bcrypt`
- Admin-Bereich (`/admin/users`) zum Anlegen weiterer Nutzer, inkl. Admin-Checkbox
- Rechtetrennung: normale Nutzer bekommen 403 auf Admin-Seiten
- Katalog-Scan (`/admin/videos`): liest `VIDEOS_DIR` rekursiv ein, holt Dauer/Codec
  per `ffprobe`, hält die `videos`-Tabelle mit dem Dateisystem synchron
- Thumbnails: automatisch per `ffmpeg`-Frame beim Scan erzeugt, Admins können pro
  Video ein eigenes PNG/JPG hochladen (`/admin/videos/<id>/thumbnail`) - überschreibt
  das automatische Bild dauerhaft, auch über künftige Scans hinweg
- Admin kann Video-Titel umbenennen (`/admin/videos/<id>/rename`)
- Video-Zuweisung (`/admin/users/<id>/permissions`): befüllt die `permissions`-Tabelle,
  das Dashboard (`/`) zeigt jedem Nutzer nur seine freigegebenen Videos
- Player (`/watch/<id>`) mit Range-Request-fähigem Streaming-Endpoint (`/stream/<id>`),
  serverseitig berechtigungsgeprüft
- VAAPI-Transcoding: HEVC/H.265-Videos werden beim ersten Abspielen per Intel-GPU
  einmalig nach H.264 transkodiert und in `data/transcoded/` gecacht - browserkompatible
  Codecs (h264/vp8/vp9/av1) laufen direkt per Direct Play
- Marker setzen/anzeigen/löschen pro Nutzer und Video, mit Sprung-zu-Zeitpunkt im Player.
  Beschreibung ist Pflicht (was soll der Kunde an dieser Stelle anders/gut finden)
- Kommentare pro Video: sichtbar für alle Nutzer mit Zugriff (nicht privat wie Marker),
  für Status-Austausch zwischen Admin und Nutzern. Löschen: eigener Kommentar oder Admin
- Admin-Übersicht über alle Marker aller Nutzer (`/admin/markers`)
- Eigenes Passwort ändern (`/account`, für alle eingeloggten Nutzer)
- Kontaktdaten (E-Mail, Telefon) pro Nutzer, selbstständig unter `/account` pflegbar,
  in der Nutzerverwaltung für Admins sichtbar
- Bearbeiter-Zuweisung: Admins können pro Kunde einen zuständigen Admin ("Bearbeiter")
  festlegen (Nutzerverwaltung, Dropdown) - Grundlage für den späteren Mailversand
- Bulk-Zuweisung nach Ordner: `/admin/users/<id>/permissions` gruppiert Videos nach
  Ordner (z.B. Datumsordner der DJI-Clips) mit einer "alle in diesem Ordner"-Checkbox
- Mailversand: schreibt ein Kunde (kein Admin) einen Kommentar, bekommt sein
  zugewiesener Bearbeiter automatisch eine Mail. Ohne konfiguriertes SMTP bleibt
  der Versand einfach aus, kein Fehler
- Admin-Einstellungsseite (`/admin/settings`): SMTP-Zugangsdaten (Gmail, web.de,
  GMX, eigener Server, ...) und die öffentliche App-URL bequem im Web-UI pflegen,
  inkl. "Test-Mail senden". Werte landen in der `settings`-Tabelle, `.env` dient
  nur noch als Fallback/Erstkonfiguration, bis jemand die Oberfläche nutzt
- Branding: App-Name, Logo und Favicon lassen sich unter `/admin/settings`
  hochladen (PNG/JPG/SVG bzw. PNG/ICO/SVG fürs Favicon), landen in `data/branding/`
  und werden über `/branding/logo` bzw. `/branding/favicon` ausgeliefert - auch
  auf der Login-Seite sichtbar
- Dunkles UI ohne Frameworks (reines CSS)

## Projektstruktur

```
app/
  main.py                 - FastAPI-App, Middleware, Startup-Logik
  database.py             - SQLite-Verbindung + Schema
  auth.py                 - Passwort-Hashing, Session-Handling, Login-Dependencies
  catalog.py               - Katalog-Scan (ffprobe)
  media.py                  - Gemeinsame Berechtigungsprüfung (Player/Streaming)
  transcode.py                - VAAPI-Transcoding + Cache
  mailer.py                     - SMTP-Mailversand
  settings.py                    - Key-Value-Einstellungen (DB, mit .env-Fallback)
  branding.py                     - Logo/Favicon-Uploads
  templates_env.py                  - gemeinsame Jinja2Templates-Instanz für alle Router
  routers/
    auth_routes.py             - /login, /logout
    dashboard.py                 - / (Übersicht), /watch/<id> (Player)
    admin.py                      - /admin/users, /admin/videos, Zuweisung
    streaming.py                   - /stream/<id>
    markers.py                      - Marker anlegen/löschen
    settings.py                      - /admin/settings
    branding.py                       - /branding/<logo|favicon>
  templates/                        - Jinja2-HTML-Templates
  static/css/style.css               - Styling
data/                                 - app.db, transcoded/, thumbnails/, branding/ (automatisch angelegt)
```
