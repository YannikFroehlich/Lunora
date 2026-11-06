#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Repository, password and optional ping URL come from the unit's EnvironmentFile
# /etc/lunora/offsite.env. restic encrypts on this host, so the target only sees ciphertext.
BACKUP_ROOT="/srv/lunora/backups"
APP_DIR="/srv/lunora/app"
STAGING_DIR="${BACKUP_ROOT}/.offsite"
MAX_LOCAL_BACKUP_AGE_MINUTES=$((26 * 60))

# A stale local backup means the nightly backup is broken: fail visibly rather than
# keep uploading an old dump.
latest="$(
    find "${BACKUP_ROOT}" \
        -mindepth 1 \
        -maxdepth 1 \
        -type d \
        -name 'lunora-*' \
        -mmin "-${MAX_LOCAL_BACKUP_AGE_MINUTES}" |
        sort |
        tail -n 1
)"
if [[ -z "${latest}" ]]; then
    echo "Kein lokales Backup der letzten ${MAX_LOCAL_BACKUP_AGE_MINUTES} Minuten gefunden." >&2
    exit 1
fi

(
    cd "${latest}"
    sha256sum --check --quiet SHA256SUMS
)
pg_restore --list "${latest}/database.dump" > /dev/null

# restic groups snapshots by host and paths. A stable path keeps parent detection and the
# retention below working, which a timestamped directory name would break every night.
mkdir -p "${STAGING_DIR}"
ln -f "${latest}/database.dump" "${STAGING_DIR}/database.dump"

# The upload directories go in unpacked, not as uploads.tar.gz: restic only deduplicates
# plain files, and a nightly full archive would not fit a home uplink.
restic backup --tag lunora \
    "${STAGING_DIR}/database.dump" \
    "${APP_DIR}/media" \
    "${APP_DIR}/private_media" \
    /etc/lunora
restic forget --tag lunora --prune --keep-daily 14 --keep-weekly 8 --keep-monthly 12
restic check --read-data-subset=5%

if [[ -n "${OFFSITE_PING_URL:-}" ]]; then
    curl --fail --silent --show-error --max-time 10 --retry 3 --output /dev/null \
        "${OFFSITE_PING_URL}" || echo "Ping fehlgeschlagen, Backup selbst ist in Ordnung." >&2
fi

echo "Offsite-Backup abgeschlossen: ${latest}"
