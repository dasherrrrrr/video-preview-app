#!/bin/sh
# Auf dem Server ausführen, um auf den neuesten Stand von GitHub zu aktualisieren
# und den Container neu zu bauen. docker-compose.override.yml (server-spezifische
# Secrets/Pfade) bleibt davon unberührt, da sie nicht in Git liegt.
set -e
git pull
docker compose up -d --build
