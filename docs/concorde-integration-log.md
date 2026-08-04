# Integrations-Log: Video-Preview-App ↔ Concorde Manager (Lovable)

Zusammenfassung der Session, in der die Video-Preview-App an das externe
CRM/Kundenportal "Concorde Manager" (gebaut mit Lovable) angebunden wurde.
Gedacht als Gedächtnisstütze, falls der ursprüngliche Chat-Verlauf mal
komprimiert/gelöscht wird - dieses Dokument lebt im Git-Repo und bleibt
unabhängig davon erhalten.

## Ausgangslage

Concorde Manager ist ein internes Content-/CRM-Tool (React/TanStack Start,
Supabase-Backend) für die Kundenverwaltung. Ziel: Kunden sollen ihre
Video-Previews (Drohnenaufnahmen etc.) direkt in Concorde ansehen und
kommentieren/markieren können, statt sich separat in dieser App einzuloggen.

## Was auf Seiten der Video-Preview-App gemacht wurde (dieses Repo)

Alles committed und auf GitHub (`dasherrrrrr/video-preview-app`, `master`).

1. **Token-basierte Kunden-API** (`app/routers/api.py`, `app/api_auth.py`)
   - `GET /api/videos`, `GET /api/videos/{id}` (inkl. Marker/Kommentare)
   - `GET /api/thumbnail/{id}`, `GET /api/stream/{id}` (Range-fähig)
   - `POST/DELETE /api/videos/{id}/markers`, `.../comments`
   - Auth: `Authorization: Bearer <token>`, für `<video>`/`<img>`-Tags
     zusätzlich per `?t=<token>` Query-Parameter (kein Header möglich)
2. **Token-basierte Admin-API** (`app/routers/api_admin.py`, `app/customers.py`)
   - Kunden anlegen/auflisten, Kunden-Token erzeugen/widerrufen
   - Katalog auflisten, Zuweisung pro Kunde lesen/setzen
   - `POST /api/admin/scan` - Katalog-Rescan von außen auslösbar
3. **Kunden-Login entfernt** - nur noch Admins/Bearbeiter loggen sich direkt
   ein (`/login` blockt Nicht-Admins). `/` leitet je nach Rolle weiter.
   Neue Admin-only Seite `/admin/videos/{id}` für Bearbeiter, um
   Kunden-Kommentare zu sehen/beantworten (Mail-Link zeigt jetzt dorthin).
4. **CORS** global aktiviert (inkl. Preflight für POST/DELETE), da die Auth
   über Bearer-Token läuft, nicht Cookies - unbedenklich mit Wildcard-Origin.
5. **Transcoding-Fixes**
   - 10-Bit-HEVC-Quellen (DJI Main10/P010) scheiterten an `h264_vaapi`
     ("No usable encoding profile found") → `scale_vaapi=format=nv12` behebt das
   - Zielauflösung auf **720p** reduziert (schneller, kleinere Cache-Dateien)
   - Katalog-Scan transkodiert neue Videos jetzt **vorab**, statt beim ersten
     Kundenaufruf zu blockieren
6. Entfernte Videos (Datei nicht mehr im Verzeichnis) werden per
   `ON DELETE CASCADE` automatisch auch bei allen Kunden aus der Zuweisung
   entfernt - kein manueller Schritt nötig.
7. README auf aktuellen Stand gebracht (bewusst ohne Produktnamen "Concorde",
   damit die App generisch für beliebige Frontends dokumentiert bleibt).

## Was auf Seiten von Concorde/Lovable gemacht wurde

Alles per Chat-Anweisung an den Lovable-Agenten im Projekt `CONCORDE MANAGER`
(`c1802713-cf1d-4948-9652-45fa4e8bc8a3`) umgesetzt, nicht in diesem Repo.

1. **DB-Migrationen**: `app_settings.video_app_url` (konfigurierbare
   Basis-URL statt hartkodiert), `clients.video_app_customer_id`,
   `clients.video_app_token_cipher` (verschlüsselter Kunden-Token)
2. **Server-Integration**: `video-preview.server.ts` (Admin-HTTP-Client),
   `video-preview.functions.ts` (Server-Functions für alle Aktionen)
3. **Admin-Settings-Karte**: URL der Video-Preview-App einstellbar
4. **Zuweisungs-UI** (`clients.$clientId.video.preview.tsx`): Video-Katalog
   nach Ordnerstruktur gruppiert (mehrstufig, mit Einrückung), Kunden-Token
   automatisch erzeugt, "Verzeichnisse neu einlesen"-Button
5. **Video-Ordner-Filter**: Feld `video_folder_prefix` pro Kunde (Onboarding
   + Kunden-Einstellungen), als Dropdown mit echten Ordnerpfaden aus dem
   Katalog befüllt (nicht mehr Freitext). Beim Speichern der Zuweisung wird
   **immer** nur die Schnittmenge aus Auswahl und sichtbarem (gefiltertem)
   Katalog gespeichert - alte Zuweisungen außerhalb des Ordners fallen
   automatisch raus, jedes Mal, ohne Sonderfall
6. **Kunden-Player**: Video, Marker-Liste, Kommentare - direkter `fetch()`
   gegen die Kunden-API mit dem geladenen Token. Autoplay-Bug gefixt (Klick
   auf Play-Overlay öffnete nur den Player, startete das Video aber nicht
   automatisch - jetzt `autoPlay` + abgesicherter `.play()`-Fallback)
7. **Secret hinterlegt**: `VIDEO_APP_ADMIN_TOKEN` in Lovable Cloud → Secrets
   (Wert nicht hier dokumentiert - bei Bedarf neu erzeugen über
   `/admin/users` in der Video-Preview-App)

## Sicherheitsreview (Concorde, nicht video-preview-app)

Ein `/api/admin/*`-Review ergab: Admin-Token bleibt sauber serverseitig,
kein Leak in den Client-Bundle. **Unabhängig davon** hat ein allgemeiner
Security-Scan von Concorde selbst 10 Probleme gefunden:

- Gefixt: Dependency-Schwachstelle (`seroval`/TanStack, kritisch eingestuft)
- **Noch offen (bewusst zurückgestellt, nicht meine Entscheidung gewesen sie
  zu ignorieren - der Nutzer wollte das separat angehen):**
  - 3x Critical: Branding/Config von jedem eingeloggten Nutzer lesbar;
    jeder eingeloggte Nutzer kann alle Finanz-/Vertriebsdaten aller Kunden
    lesen/ändern; unauthentifizierter Endpunkt kann Social-Media-Publishing
    auslösen (`src/routes/api/public/hooks/publish-due-posts.ts`)
  - 6x Warning: SSRF bei Server-seitigem Fetch nutzergesteuerter URLs,
    IT-Inventar für alle Mitarbeiter lesbar, RLS-Policies mit
    `SECURITY DEFINER`/`Always True`

## Social-Media-Publishing (Recherche, nichts umgesetzt)

Concorde hat bereits Datenmodell + Teil-Implementierung für Instagram/
Facebook/LinkedIn/TikTok (`social_connections`, `social_posts`,
`social-providers.server.ts`). Instagram (inkl. Reels/Video) ist am
weitesten. Blockiert aktuell an:

- Fehlenden Secrets `META_APP_ID`/`META_APP_SECRET` (Anleitung dafür wurde
  als PDF an den Nutzer geschickt - Meta-App vom Typ "Business" mit
  Produkten "Facebook Login" + "Instagram Graph API", Redirect-URI
  `https://<domain>/api/public/social/instagram/callback`)
- Fehlendem Meta-App-Review für `instagram_content_publish` etc.
- Kaputtem Bild-Upload bei LinkedIn, nicht implementiertem TikTok
- Dem oben genannten unauthentifizierten Hook-Endpunkt

## Infrastruktur-Hinweise

- Aktuelles Test-System: Unraid-Server "NASty" (AMD EPYC 7551P), GPU-Passthrough
  über Intel Arc A380 (`/dev/dri/renderD128`), VAAPI-Transcoding verifiziert
  funktionsfähig inkl. 10-Bit-Quellen nach dem `scale_vaapi`-Fix
- Server wurde während der Session neu aufgesetzt (SSH-Host-Key musste neu
  autorisiert werden, Docker-Compose-Container-Zuordnung ging verloren,
  altes Videomaterial aus einem Ordner war nach dem Reinstall nicht mehr
  vorhanden - Kunden-Zuweisung wurde auf vorhandenes Material umgestellt)
- Geplanter Produktiv-Umzug: entweder Mini-PC mit Intel N150 oder
  HPE DL380 Gen9 + zusätzliche GPU. Bei der erwarteten Größenordnung (bis
  zu 20 Videos à bis zu 2 Stunden Laufzeit) wurde zum DL380 Gen9 tendiert,
  da Xeons keine iGPU haben und die Arc-Medien-Engine mehr Durchsatz für
  lange Vortranskodierungen bietet. Für Single-Slot-Anforderungen gibt es
  keine A580 in Single-Slot - Alternativen sind A380 (schwächer) oder die
  neuere Sparkle Arc Pro B50 Blower (72W, Single-Slot, kein Extra-Stromstecker)

## Bekannte offene Punkte / Aufräumarbeiten

- Test-Unterordner `20260111/testunterordner/DJI_test_clip.MP4` (Video-ID 11
  im Katalog) liegt noch auf dem Server - war nur zum Testen der
  mehrstufigen Ordner-Auswahl, kann bei Bedarf gelöscht werden (danach
  einmal "Verzeichnisse neu einlesen" ausführen, damit der Katalog-Eintrag
  automatisch mit entfernt wird)
- Die 9 verbleibenden Security-Probleme in Concorde sind unangetastet
- Meta-App/Secrets für Social-Publishing stehen noch aus
