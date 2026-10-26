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

# Validate the new commit outside the live checkout first, so a failing pip install or
# deploy check or a model change without its migration leaves the running release untouched.
# Once the checkout holds the new code, Gunicorn workers recycled by --max-requests import it
# from disk, with or without its dependencies. Whether migrations apply cleanly is not covered:
# that can only be checked by applying them.
preflight_dir=$(mktemp -d)
trap 'rm -rf -- "${preflight_dir}"' EXIT
# Not `git archive | tar -x`: under lunora-auto-deploy.service's RestrictSUIDSGID=true, GNU tar
# 1.35 on Ubuntu 26.04 fails every file with "Function not implemented". A throwaway index
# keeps the live checkout's index untouched.
GIT_INDEX_FILE="${preflight_dir}/index" git read-tree origin/main
GIT_INDEX_FILE="${preflight_dir}/index" git checkout-index --all --prefix="${preflight_dir}/app/"
python3 -m venv "${preflight_dir}/venv"
"${preflight_dir}/venv/bin/python" -m pip install --require-virtualenv --quiet --disable-pip-version-check \
    -r "${preflight_dir}/app/requirements.txt" -c "${preflight_dir}/app/constraints.txt"
(
    set -a
    source "${ENV_FILE}"
    set +a
    cd "${preflight_dir}/app"
    "${preflight_dir}/venv/bin/python" manage.py check --deploy
    "${preflight_dir}/venv/bin/python" manage.py makemigrations --check --dry-run
)
echo "Vorabprüfung für $(git rev-parse --short origin/main) erfolgreich."

git switch main
git merge --ff-only origin/main

ensure_deployment_directory() {
    local path="$1"
    local mode="$2"

    if [[ -e "${path}" && ! -d "${path}" ]]; then
        echo "Abbruch: ${path} existiert, ist aber kein Verzeichnis." >&2
        exit 1
    fi

    if [[ ! -d "${path}" ]]; then
        install -d -m "${mode}" -g lunora "${path}"
    elif [[ ! -w "${path}" ]]; then
        echo "Abbruch: ${path} ist für den Deployment-Benutzer nicht beschreibbar." >&2
        exit 1
    fi
}

ensure_deployment_directory media 2770
ensure_deployment_directory private_media 2770
ensure_deployment_directory staticfiles 2750

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    python3 -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m pip install --require-virtualenv --upgrade-strategy only-if-needed -r requirements.txt -c constraints.txt

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
