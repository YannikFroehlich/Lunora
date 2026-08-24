#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/srv/lunora/app"
DEPLOY_USER="yunnik"
STATE_DIR="/var/lib/lunora-auto-deploy"
RUNTIME_DIR="/run/lunora-auto-deploy"
SUCCESS_STATE="${STATE_DIR}/last-successful-commit"
BACKUP_STATE="${STATE_DIR}/last-backed-up-commit"
LOCK_FILE="${RUNTIME_DIR}/deploy.lock"

if [[ ${EUID} -ne 0 ]]; then
    echo "Der automatische Deployment-Treiber muss als root laufen." >&2
    exit 1
fi

if ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
    echo "Deployment-Benutzer ${DEPLOY_USER} existiert nicht." >&2
    exit 1
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
    echo "Produktions-Checkout ${APP_DIR} fehlt." >&2
    exit 1
fi

install -d -o root -g root -m 0750 "${STATE_DIR}" "${RUNTIME_DIR}"

exec 9>"${LOCK_FILE}"
if ! flock --nonblock 9; then
    echo "Ein Lunora-Deployment läuft bereits; dieser Durchlauf wird übersprungen."
    exit 0
fi

as_deployer() {
    runuser --user "${DEPLOY_USER}" -- \
        env \
        HOME="/home/${DEPLOY_USER}" \
        USER="${DEPLOY_USER}" \
        LOGNAME="${DEPLOY_USER}" \
        GIT_TERMINAL_PROMPT=0 \
        PIP_NO_CACHE_DIR=1 \
        "$@"
}

read_state() {
    local state_file="$1"
    local value=""

    if [[ -s "${state_file}" ]]; then
        IFS= read -r value < "${state_file}"
    fi

    if [[ -n "${value}" && ! "${value}" =~ ^[0-9a-f]{40}$ ]]; then
        echo "Ungültiger Deployment-Status in ${state_file}." >&2
        exit 1
    fi

    printf '%s' "${value}"
}

write_state() {
    local state_file="$1"
    local value="$2"
    local temporary_file

    temporary_file=$(mktemp "${state_file}.tmp.XXXXXX")
    printf '%s\n' "${value}" > "${temporary_file}"
    chmod 0600 "${temporary_file}"
    mv --force "${temporary_file}" "${state_file}"
}

if [[ -n "$(as_deployer git -C "${APP_DIR}" status --porcelain)" ]]; then
    echo "Abbruch: Der Produktions-Checkout enthält lokale Änderungen." >&2
    exit 1
fi

as_deployer git -C "${APP_DIR}" fetch --quiet --prune origin main

current_commit=$(as_deployer git -C "${APP_DIR}" rev-parse --verify HEAD)
remote_commit=$(as_deployer git -C "${APP_DIR}" rev-parse --verify origin/main)
successful_commit=$(read_state "${SUCCESS_STATE}")

if [[ -z "${successful_commit}" && "${current_commit}" == "${remote_commit}" ]]; then
    write_state "${SUCCESS_STATE}" "${current_commit}"
    echo "Auto-Deployment initialisiert bei ${current_commit:0:12}."
    exit 0
fi

if [[ "${remote_commit}" == "${successful_commit}" ]]; then
    echo "Lunora ist bereits auf dem erfolgreich geprüften main-Commit ${remote_commit:0:12}."
    exit 0
fi

if ! as_deployer git -C "${APP_DIR}" merge-base --is-ancestor \
    "${current_commit}" "${remote_commit}"; then
    echo "Abbruch: origin/main ist kein Fast-Forward des Produktions-Checkouts." >&2
    exit 1
fi

backed_up_commit=$(read_state "${BACKUP_STATE}")
if [[ "${backed_up_commit}" != "${remote_commit}" ]]; then
    echo "Erstelle vor dem Deployment ein Backup für ${remote_commit:0:12}."
    systemctl start lunora-backup.service
    write_state "${BACKUP_STATE}" "${remote_commit}"
fi

echo "Deploye origin/main ${remote_commit:0:12}."
as_deployer env LUNORA_RESTART_SERVICES=false "${APP_DIR}/scripts/deploy.sh"

deployed_commit=$(as_deployer git -C "${APP_DIR}" rev-parse --verify HEAD)

systemctl restart lunora-web.service
systemctl restart lunora-automations.service
systemctl is-active --quiet lunora-web.service lunora-automations.service

curl \
    --fail \
    --silent \
    --show-error \
    --output /dev/null \
    --header 'Host: lunora.yfserver.de' \
    --header 'X-Forwarded-Proto: https' \
    http://127.0.0.1:8080/login/

write_state "${SUCCESS_STATE}" "${deployed_commit}"
echo "Lunora ${deployed_commit:0:12} wurde automatisch deployt und geprüft."
