#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Dieses Skript muss mit sudo ausgeführt werden." >&2
    exit 1
fi

DEPLOY_USER="${SUDO_USER:-yunnik}"
if ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
    echo "Deployment-Benutzer ${DEPLOY_USER} existiert nicht." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install --yes \
    ca-certificates \
    curl \
    git \
    nginx \
    postgresql \
    postgresql-contrib \
    python3-pip \
    python3-venv \
    redis-server \
    unattended-upgrades

install -d -m 0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
    -o /usr/share/keyrings/cloudflare-main.gpg
chmod 0644 /usr/share/keyrings/cloudflare-main.gpg
printf '%s\n' \
    'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
    > /etc/apt/sources.list.d/cloudflared.list
apt-get update
apt-get install --yes cloudflared

if ! id lunora >/dev/null 2>&1; then
    useradd --system --home-dir /srv/lunora --shell /usr/sbin/nologin --no-create-home lunora
fi

usermod --append --groups lunora "${DEPLOY_USER}"
usermod --append --groups lunora www-data

install -d -o root -g lunora -m 0750 /srv/lunora
install -d -o "${DEPLOY_USER}" -g lunora -m 2750 /srv/lunora/app
install -d -o "${DEPLOY_USER}" -g lunora -m 2750 /srv/lunora/venv
install -d -o root -g root -m 0700 /srv/lunora/backups
install -d -o root -g lunora -m 0750 /etc/lunora

systemctl enable --now postgresql redis-server nginx

echo "Bootstrap abgeschlossen. Bitte einmal neu per SSH anmelden, damit die neue Gruppenzugehörigkeit gilt."
