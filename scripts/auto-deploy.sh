#!/usr/bin/env bash
set -Eeuo pipefail

# The defaults are the production instance. The dev instance (DEPLOYMENT.md, "Dev-Instanz")
# overrides them through LUNORA_* environment variables set in its systemd unit; they are
# passed on to scripts/deploy.sh unchanged.
APP_DIR="${LUNORA_APP_DIR:-/srv/lunora/app}"
export LUNORA_APP_DIR="${APP_DIR}"
BRANCH="${LUNORA_BRANCH:-main}"
DEPLOY_USER="yunnik"
APP_USER="${LUNORA_APP_USER:-lunora}"
STATE_DIR="${LUNORA_STATE_DIR:-/var/lib/lunora-auto-deploy}"
RUNTIME_DIR="${LUNORA_RUNTIME_DIR:-/run/lunora-auto-deploy}"
read -r -a SERVICES <<< "${LUNORA_SERVICES:-lunora-web.service lunora-automations.service}"
# Empty disables the pre-deploy backup (the dev instance has none).
BACKUP_SERVICE="${LUNORA_BACKUP_SERVICE-lunora-backup.service}"
HEALTH_HOST="${LUNORA_HOSTNAME:-lunora.yfserver.de}"
HEALTH_URL="${LUNORA_HEALTH_URL:-http://127.0.0.1:8090/login/}"
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

if ! id "${APP_USER}" >/dev/null 2>&1; then
    echo "Anwendungsbenutzer ${APP_USER} existiert nicht." >&2
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

as_deployer git -C "${APP_DIR}" fetch --quiet --prune origin "${BRANCH}"

current_commit=$(as_deployer git -C "${APP_DIR}" rev-parse --verify HEAD)
remote_commit=$(as_deployer git -C "${APP_DIR}" rev-parse --verify "origin/${BRANCH}")
successful_commit=$(read_state "${SUCCESS_STATE}")

if [[ -z "${successful_commit}" && "${current_commit}" == "${remote_commit}" ]]; then
    write_state "${SUCCESS_STATE}" "${current_commit}"
    echo "Auto-Deployment initialisiert bei ${current_commit:0:12}."
    exit 0
fi

if [[ "${remote_commit}" == "${successful_commit}" ]]; then
    echo "Lunora ist bereits auf dem erfolgreich geprüften ${BRANCH}-Commit ${remote_commit:0:12}."
    exit 0
fi

if ! as_deployer git -C "${APP_DIR}" merge-base --is-ancestor \
    "${current_commit}" "${remote_commit}"; then
    echo "Abbruch: origin/${BRANCH} ist kein Fast-Forward des Checkouts ${APP_DIR}." >&2
    exit 1
fi

backed_up_commit=$(read_state "${BACKUP_STATE}")
if [[ -n "${BACKUP_SERVICE}" && "${backed_up_commit}" != "${remote_commit}" ]]; then
    echo "Erstelle vor dem Deployment ein Backup für ${remote_commit:0:12}."
    systemctl start "${BACKUP_SERVICE}"
    write_state "${BACKUP_STATE}" "${remote_commit}"
fi

echo "Deploye origin/${BRANCH} ${remote_commit:0:12}."
as_deployer env LUNORA_RESTART_SERVICES=false "${APP_DIR}/scripts/deploy.sh"

deployed_commit=$(as_deployer git -C "${APP_DIR}" rev-parse --verify HEAD)

if ! as_deployer git -C "${APP_DIR}" ls-files -z | \
    runuser --user "${APP_USER}" -- \
        env APP_DIR="${APP_DIR}" bash -c '
            while IFS= read -r -d "" path; do
                if [[ ! -r "${APP_DIR}/${path}" ]]; then
                    echo "Abbruch: ${path} ist für den Anwendungsdienst nicht lesbar." >&2
                    exit 1
                fi
            done
        '
then
    echo "Abbruch: Der Produktions-Checkout ist für ${APP_USER} nicht vollständig lesbar." >&2
    exit 1
fi

for service in "${SERVICES[@]}"; do
    systemctl restart "${service}"
done
systemctl is-active --quiet "${SERVICES[@]}"

curl \
    --fail \
    --silent \
    --show-error \
    --output /dev/null \
    --header "Host: ${HEALTH_HOST}" \
    --header 'X-Forwarded-Proto: https' \
    "${HEALTH_URL}"

write_state "${SUCCESS_STATE}" "${deployed_commit}"
echo "Lunora ${deployed_commit:0:12} wurde automatisch deployt und geprüft."
