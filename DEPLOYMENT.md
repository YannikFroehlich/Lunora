# Lunora auf Ubuntu bereitstellen

Ziel: `lunora.yfserver.de` läuft über einen ausgehenden Cloudflare Tunnel. Nginx
lauscht nur auf `127.0.0.1:8080`, Gunicorn nur auf einem Unix-Socket. Lokal bleibt
SQLite in Verwendung; Produktion verwendet die frische PostgreSQL-Datenbank.

## Noch benötigte Geheimnisse

- Django Secret Key (neu und zufällig)
- PostgreSQL-Passwort für die Rolle `lunora` (neu und zufällig)
- STRATO-SMTP-Passwort für `webmaster@yfserver.de`
- Cloudflare-Turnstile-Site-Key und -Secret für `lunora.yfserver.de`
- Cloudflare-Tunnel-Token; er wird nur beim Installieren des Dienstes verwendet
- Passwort des initialen Django-Superusers `yannik` (interaktiv festlegen)
- optional später: OpenWeather API Key

Keiner dieser Werte gehört in Git. Die produktive Datei liegt ausschließlich unter
`/etc/lunora/lunora.env` mit Besitzer `root:lunora` und Modus `0640`.

## Reihenfolge

1. Änderungen auf `develop` testen, committen und nach `main` übernehmen.
2. `scripts/bootstrap-ubuntu.sh` einmal mit `sudo` ausführen und neu per SSH anmelden.
3. Das öffentliche GitHub-Repository als `/srv/lunora/app` aus Branch `main` klonen.
4. PostgreSQL-Rolle und leere Datenbank `lunora` anlegen.
5. `deploy/lunora.env.example` nach `/etc/lunora/lunora.env` kopieren und Secrets einsetzen.
6. Virtuelle Umgebung, Python-Pakete, Migrationen und statische Dateien einrichten.
7. systemd-Units, Nginx-Konfiguration und Backup-Skript installieren.
8. Superuser `yannik` mit `webmaster@yfserver.de` anlegen.
9. Cloudflare Turnstile und anschließend den Tunnel zu `http://localhost:8080` einrichten.
10. Django-, E-Mail-, Upload-, Automations-, Backup- und externen HTTPS-Test durchführen.
11. Erst nach erfolgreichem Test Passwort-SSH deaktivieren und die Firewall festziehen.

## Installation der versionierten Systemdateien

```bash
sudo install -o root -g root -m 0644 deploy/lunora-web.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/lunora-automations.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/lunora-purge.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/lunora-purge.timer /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/lunora-backup.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/lunora-backup.timer /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/nginx-lunora.conf /etc/nginx/sites-available/lunora
sudo ln -s /etc/nginx/sites-available/lunora /etc/nginx/sites-enabled/lunora
sudo rm -f /etc/nginx/sites-enabled/default
sudo install -o root -g root -m 0750 scripts/backup.sh /usr/local/sbin/lunora-backup
sudo systemctl daemon-reload
sudo nginx -t
sudo systemctl enable --now lunora-web.service lunora-automations.service
sudo systemctl enable --now lunora-purge.timer lunora-backup.timer
sudo systemctl restart nginx
```

## Initialer Superuser

```bash
set -a
source /etc/lunora/lunora.env
set +a
/srv/lunora/venv/bin/python /srv/lunora/app/manage.py createsuperuser \
    --username yannik \
    --email webmaster@yfserver.de
```

## Cloudflare

Im Dashboard einen Turnstile-Widget für ausschließlich `lunora.yfserver.de` erstellen
und beide Keys in `/etc/lunora/lunora.env` setzen. Danach einen remotely-managed Tunnel
erstellen, den vom Dashboard angezeigten Linux-Installationsbefehl einmal ausführen und
den öffentlichen Hostnamen so routen:

```text
Hostname: lunora.yfserver.de
Service:  http://localhost:8080
```

Cloudflare Access darf nicht vor die gesamte Website geschaltet werden, weil die
Registrierung öffentlich bleiben soll. WAF, Bot-Schutz und Rate Limits können dagegen
für Login, Registrierung und Passwort-Reset aktiviert werden.

## Prüfungen

```bash
set -a
source /etc/lunora/lunora.env
set +a
/srv/lunora/venv/bin/python /srv/lunora/app/manage.py check --deploy
systemctl --no-pager --full status lunora-web lunora-automations nginx redis-server postgresql cloudflared
systemctl list-timers --all | grep lunora
curl --fail --header 'Host: lunora.yfserver.de' http://127.0.0.1:8080/login/
sudo systemctl start lunora-backup.service
sudo journalctl -u lunora-backup.service --no-pager -n 50
```

Extern müssen `https://lunora.yfserver.de/login/`, Registrierung, Passwort-Reset,
Profilbild, privater Notizanhang, E-Mail-Versand und ein Neustart des Servers getestet
werden. Lokale Backups schützen nicht vor einem Defekt oder Verlust des Servers; später
sollte mindestens eine verschlüsselte Kopie auf ein zweites System ergänzt werden.
