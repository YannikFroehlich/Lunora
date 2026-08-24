#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

BACKUP_ROOT="/srv/lunora/backups"
APP_DIR="/srv/lunora/app"
DATABASE_NAME="${DJANGO_DB_NAME:-lunora}"
STAMP="$(date --utc +'%Y%m%dT%H%M%SZ')"
FINAL_DIR="${BACKUP_ROOT}/lunora-${STAMP}"
WORK_DIR="$(mktemp -d "${BACKUP_ROOT}/.lunora-${STAMP}-XXXXXX")"

cleanup() {
    if [[ -n "${WORK_DIR:-}" && "${WORK_DIR}" == "${BACKUP_ROOT}/.lunora-"* ]]; then
        rm -rf -- "${WORK_DIR}"
    fi
}
trap cleanup EXIT

runuser -u postgres -- pg_dump --format=custom "${DATABASE_NAME}" > "${WORK_DIR}/database.dump"
tar --create --gzip --file "${WORK_DIR}/uploads.tar.gz" --directory "${APP_DIR}" media private_media
(
    cd "${WORK_DIR}"
    sha256sum database.dump uploads.tar.gz > SHA256SUMS
)
mv "${WORK_DIR}" "${FINAL_DIR}"
WORK_DIR=""

find "${BACKUP_ROOT}" \
    -mindepth 1 \
    -maxdepth 1 \
    -type d \
    -name 'lunora-*' \
    -mtime +14 \
    -exec rm -rf -- {} +

echo "Backup erstellt: ${FINAL_DIR}"
