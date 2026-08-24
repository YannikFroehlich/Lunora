#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/srv/lunora/app"
VENV_DIR="/srv/lunora/venv"
ENV_FILE="/etc/lunora/lunora.env"
RESTART_SERVICES="${LUNORA_RESTART_SERVICES:-true}"

if [[ ${EUID} -eq 0 ]]; then
    echo "Das Deployment darf nicht als root ausgeführt werden." >&2
    exit 1
fi

if [[ "${RESTART_SERVICES}" != "true" && "${RESTART_SERVICES}" != "false" ]]; then
    echo "LUNORA_RESTART_SERVICES muss true oder false sein." >&2
    exit 1
fi

cd "${APP_DIR}"

if [[ -n "$(git status --porcelain)" ]]; then
    echo "Abbruch: Der Produktions-Checkout enthält lokale Änderungen." >&2
    exit 1
fi

git fetch --prune origin main
git switch main
git merge --ff-only origin/main

install -d -m 2770 -g lunora media private_media
install -d -m 2750 -g lunora staticfiles

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    python3 -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m pip install --require-virtualenv --upgrade-strategy only-if-needed -r requirements.txt

set -a
source "${ENV_FILE}"
set +a

"${VENV_DIR}/bin/python" manage.py check --deploy
"${VENV_DIR}/bin/python" manage.py migrate --noinput
"${VENV_DIR}/bin/python" manage.py collectstatic --noinput

if [[ "${RESTART_SERVICES}" == "true" ]]; then
    sudo systemctl restart lunora-web.service
    sudo systemctl restart lunora-automations.service
    systemctl --no-pager --full status lunora-web.service
else
    echo "Dienstneustart wird vom übergeordneten Deployment-Dienst ausgeführt."
fi
