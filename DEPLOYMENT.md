# Lunora auf Ubuntu bereitstellen

> **Stand 27. Oktober 2026:** Lunora ist öffentlich unter `https://lunora.yfserver.de` erreichbar,
> gehostet auf dem Heimserver `yfsrv1` hinter dem Cloudflare Tunnel `lunora-yfsrv1`. Jeder Merge
> auf `main` wird automatisch deployt. Die Abweichungen vom Zielbild stehen unter
> „Besonderheiten auf yfsrv1“.

Ziel: `lunora.yfserver.de` läuft über einen ausgehenden Cloudflare Tunnel. Nginx
lauscht nur auf `127.0.0.1:8090`, Gunicorn nur auf einem Unix-Socket. Lokal bleibt
SQLite in Verwendung; Produktion verwendet die frische PostgreSQL-Datenbank.

## Besonderheiten auf yfsrv1

`yfsrv1` betreibt außerdem Docker-Dienste (RoundRush mit Caddy auf `80`/`443` und eigenem
cloudflared, `nexora` auf `8001`, `shdashboard` auf `8080`, Home Assistant auf `8123`). Sie
dürfen nicht angefasst werden. Abweichend von der Anleitung unten gilt:

- **Bootstrap:** `scripts/bootstrap-ubuntu.sh` wurde nicht unverändert ausgeführt. Die Pakete
  wurden mit einer temporären `/usr/sbin/policy-rc.d` (`exit 101`) installiert, damit apt
  Nginx nicht startet, solange dessen Standardseite Port `80` von Caddy belegen will. Danach
  wurde `/etc/nginx/sites-enabled/default` entfernt.
- **cloudflared:** Der Dienst kommt aus `deploy/cloudflared.service` statt aus
  `cloudflared service install` (siehe „Cloudflare“). Der Tunnel-Token liegt nur in
  `/etc/cloudflared/token`.
- **E-Mail:** Noch ohne SMTP-Passwort, deshalb läuft das Console-Backend (Mails landen im
  Journal von `lunora-web`/`lunora-automations`, Passwort-Reset per Mail geht nicht). Weil
  Django 6.1 das bei `check --deploy` als Fehler meldet, steht in `/etc/lunora/lunora.env`
  `DJANGO_SILENCED_SYSTEM_CHECKS=mail.E001`. Beim Einrichten von SMTP diese Zeile entfernen.
- **Vorheriger LAN-Betrieb (26.–27. Oktober 2026):** Die damalige Env liegt als
  `/etc/lunora/lunora.env.lan-backup` auf dem Server.

## Noch benötigte Geheimnisse

- Django Secret Key (neu und zufällig)
- PostgreSQL-Passwort für die Rolle `lunora` (neu und zufällig)
- STRATO-SMTP-Passwort für `webmaster@yfserver.de`
- Cloudflare-Turnstile-Site-Key und -Secret für `lunora.yfserver.de`
- Cloudflare-Tunnel-Token; er liegt ausschließlich in `/etc/cloudflared/token`
  und wird über `--token-file` geladen
- Passwort des initialen Django-Superusers `yannik` (interaktiv festlegen)
- optional später: OpenWeather API Key
- persistentes VAPID-Schlüsselpaar für Web Push; nur der öffentliche Schlüssel darf
  im Browser sichtbar sein

Keiner dieser Werte gehört in Git. Die produktive Datei liegt ausschließlich unter
`/etc/lunora/lunora.env` mit Besitzer `root:lunora` und Modus `0640`.

## Reihenfolge

1. Änderungen auf `develop` testen, committen und nach `main` übernehmen.
2. `scripts/bootstrap-ubuntu.sh` einmal mit `sudo` ausführen und neu per SSH anmelden.
3. Das öffentliche GitHub-Repository als `/srv/lunora/app` aus Branch `main` klonen.
4. PostgreSQL-Rolle und leere Datenbank `lunora` anlegen.
5. `deploy/lunora.env.example` nach `/etc/lunora/lunora.env` kopieren und Secrets einsetzen.
6. Virtuelle Umgebung, Python-Pakete, Migrationen und statische Dateien einrichten.
7. systemd-Units, Nginx-Konfiguration, Backup-Skript und Auto-Deployment installieren.
8. Superuser `yannik` mit `webmaster@yfserver.de` anlegen.
9. Cloudflare Turnstile und anschließend den Tunnel zu `http://localhost:8090` einrichten.
10. Django-, E-Mail-, Upload-, Automations-, Backup- und externen HTTPS-Test durchführen.
11. Erst nach erfolgreichem Test Passwort-SSH deaktivieren und die Firewall festziehen.

## Web Push vorbereiten

Nach Installation der Python-Abhängigkeiten als `yunnik` ein VAPID-Schlüsselpaar in
einem privaten temporären Verzeichnis erzeugen. Die private Datei niemals im
Repository oder in einem Diagnose-Output ablegen:

```bash
umask 077
mkdir -p /home/yunnik/lunora-vapid
cd /home/yunnik/lunora-vapid
/srv/lunora/venv/bin/vapid --gen
/srv/lunora/venv/bin/vapid --applicationServerKey --private-key private_key.pem
sudo install -o root -g lunora -m 0640 private_key.pem /etc/lunora/vapid_private.pem
```

Nur den ausgegebenen Application-Server-Key als `WEB_PUSH_VAPID_PUBLIC_KEY` nach
`/etc/lunora/lunora.env` übernehmen. Dort außerdem
`WEB_PUSH_VAPID_PRIVATE_KEY=/etc/lunora/vapid_private.pem` und
`WEB_PUSH_VAPID_SUBJECT=mailto:webmaster@yfserver.de` setzen. Anschließend das private
temporäre Verzeichnis sicher entfernen und `lunora-web` sowie `lunora-automations`
neu starten. Der Automationsdienst stellt neue Inbox-Einträge zu und entfernt
abgelaufene Browser-Abonnements automatisch.

## Installation der versionierten Systemdateien

```bash
sudo install -o root -g root -m 0644 deploy/lunora-web.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/lunora-automations.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/lunora-purge.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/lunora-purge.timer /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/lunora-backup.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/lunora-backup.timer /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/lunora-auto-deploy.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/lunora-auto-deploy.timer /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/nginx-lunora.conf /etc/nginx/sites-available/lunora
sudo ln -s /etc/nginx/sites-available/lunora /etc/nginx/sites-enabled/lunora
sudo rm -f /etc/nginx/sites-enabled/default
sudo install -o root -g root -m 0750 scripts/backup.sh /usr/local/sbin/lunora-backup
sudo install -o root -g root -m 0750 scripts/auto-deploy.sh /usr/local/sbin/lunora-auto-deploy
sudo systemctl daemon-reload
sudo nginx -t
sudo systemctl enable --now lunora-web.service lunora-automations.service
sudo systemctl enable --now lunora-purge.timer lunora-backup.timer lunora-auto-deploy.timer
sudo systemctl restart nginx
sudo systemctl start lunora-auto-deploy.service
```

## Automatische Deployments von `main`

Das öffentliche Repository verwendet absichtlich keinen dauerhaft selbst gehosteten
GitHub-Actions-Runner auf dem Produktionsserver. Stattdessen prüft
`lunora-auto-deploy.timer` ausschließlich ausgehend und ohne GitHub-Token ungefähr jede
Minute `origin/main`. Jeder Push oder Merge auf `main` ist damit produktionswirksam.
Der Workflow `.github/workflows/ci.yml` führt Django- und Frontend-Tests auf isolierter
GitHub-Infrastruktur aus. Der Branchschutz von `main` blockiert direkte Pushes, auch für
Admins: Änderungen kommen nur per Pull Request von `develop` auf `main`, und der
Statuscheck `Tests` muss vor dem Merge grün sein.

Bei einem neuen Fast-Forward-Commit führt der root-eigene Treiber diese Schritte aus:

1. einen unveränderten Produktions-Checkout und Fast-Forward-Verlauf prüfen;
2. einmalig ein lokales Backup für den Ziel-Commit erstellen;
3. `scripts/deploy.sh` als Benutzer `yunnik` ohne privilegierten Dienstneustart starten. Das
   Skript prüft den neuen Commit zuerst außerhalb des Produktions-Checkouts: Export per
   Wegwerf-Index und `git checkout-index` in ein temporäres Verzeichnis (nicht `git archive | tar`,
   das unter `RestrictSUIDSGID=true` der Unit scheitert), eigene venv, `pip install -r requirements.txt -c constraints.txt`,
   `check --deploy` und `makemigrations --check --dry-run`. Erst wenn das gelingt, werden Checkout
   und produktive venv angefasst, sodass ein Paket- oder Konfigurationsfehler oder eine
   Modelländerung ohne Migration die laufende Version unverändert lässt. Ob sich die Migrationen
   fehlerfrei anwenden lassen, deckt die Vorabprüfung nicht ab; das lässt sich nur durch Anwenden
   prüfen;
4. als Benutzer `lunora` prüfen, dass sämtliche getrackten Dateien lesbar sind;
5. Web- und Automationsdienst kontrolliert als root neu starten;
6. beide Dienste und den internen HTTPS-Proxy-Pfad prüfen;
7. erst danach den Commit unter `/var/lib/lunora-auto-deploy` als erfolgreich markieren.

Die Auto-Deploy-Unit muss `UMask=0027` verwenden. Ein restriktiverer Wert wie `0077`
würde von Git neu geschriebene Dateien vor der Gruppe `lunora` verbergen und Gunicorn
am Start hindern. Der Lesbarkeitstest muss vor den Dienstneustarts bleiben.

Ein fehlgeschlagenes Deployment wird nicht als erfolgreich markiert und beim nächsten
Timerlauf erneut versucht. Diagnose und manuelle Auslösung:

```bash
systemctl list-timers lunora-auto-deploy.timer --no-pager
sudo systemctl start lunora-auto-deploy.service
sudo journalctl -u lunora-auto-deploy.service --no-pager -n 100
```

Änderungen an `scripts/auto-deploy.sh` oder den beiden Auto-Deploy-Units werden aus
Sicherheitsgründen nicht selbst als root installiert. Diese Dateien nach einer solchen
Änderung einmal manuell mit den obigen `install`-Befehlen aktualisieren und anschließend
`sudo systemctl daemon-reload` ausführen.

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
(Typ „Cloudflared“) erstellen. Den angezeigten Befehl `cloudflared service install …` **nicht**
ausführen, er schreibt den Token offen in eine eigene Unit. Stattdessen nur den Token
(die Zeichenkette `eyJ…`) in die Datei schreiben und die versionierte Unit installieren:

```bash
sudo sh -c 'umask 077; cat > /etc/cloudflared/token'   # Token einfügen, Enter, Strg+D
sudo install -o root -g root -m 0644 deploy/cloudflared.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared.service
```

Anschließend im Tunnel eine Route („Published application“) anlegen:

```text
Hostname: lunora.yfserver.de
Service:  http://localhost:8090
```

Liefert die Domain danach Cloudflare-Fehler 1033, zeigt der DNS-Eintrag noch auf einen
alten Tunnel: Unter DNS den CNAME `lunora` auf `<Tunnel-ID>.cfargotunnel.com` (Proxied)
setzen.

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
systemctl is-enabled lunora-auto-deploy.timer
curl --fail --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  --header 'Host: lunora.yfserver.de' --header 'X-Forwarded-Proto: https' \
  http://127.0.0.1:8090/login/
sudo systemctl start lunora-backup.service
sudo journalctl -u lunora-backup.service --no-pager -n 50
```

Extern müssen `https://lunora.yfserver.de/login/`, Registrierung, Passwort-Reset,
Profilbild, privater Notizanhang, E-Mail-Versand, Web Push bei geschlossenem Tab und ein Neustart des Servers getestet
werden. Lokale Backups schützen nicht vor einem Defekt oder Verlust des Servers; später
sollte mindestens eine verschlüsselte Kopie auf ein zweites System ergänzt werden.
