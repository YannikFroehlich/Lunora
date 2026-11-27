#!/usr/bin/env bash
# Replaces the dev instance's data with a copy of production (database, media,
# private_media). Production is only read. Runs as root from lunora-dev-refresh.timer;
# see DEPLOYMENT.md, "Dev-Instanz".
set -Eeuo pipefail

PROD_DB="lunora"
DEV_DB="lunora_dev"
DEV_ROLE="lunora_dev"
PROD_APP="/srv/lunora/app"
DEV_APP="/srv/lunora-dev/app"
DEV_VENV="/srv/lunora-dev/venv"
DEV_ENV="/etc/lunora-dev/lunora.env"
DEV_REDIS_DB="2"
DEV_SERVICES=(lunora-dev-web.service lunora-dev-automations.service)

if [[ ${EUID} -ne 0 ]]; then
    echo "Muss als root laufen." >&2
    exit 1
fi

# The dev instance must never send real mail: it now holds real users' addresses.
if ! grep -q '^DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend$' "${DEV_ENV}"; then
    echo "Abbruch: ${DEV_ENV} verschickt echte E-Mails; erst auf das Console-Backend umstellen." >&2
    exit 1
fi

dump=$(mktemp)
cleanup() {
    rm -f -- "${dump}"
    systemctl start "${DEV_SERVICES[@]}" lunora-dev-auto-deploy.timer || true
}
trap cleanup EXIT

# Keep a dev deploy from running in the middle of the copy.
systemctl stop lunora-dev-auto-deploy.timer
while systemctl is-active --quiet lunora-dev-auto-deploy.service; do sleep 5; done
systemctl stop "${DEV_SERVICES[@]}"

chown postgres "${dump}"
runuser --user postgres -- pg_dump --format=custom --file="${dump}" "${PROD_DB}"
runuser --user postgres -- psql -q -v ON_ERROR_STOP=1 \
    -c "drop database if exists ${DEV_DB} with (force)" \
    -c "create database ${DEV_DB} owner ${DEV_ROLE}"
runuser --user postgres -- pg_restore --no-owner --no-acl --role="${DEV_ROLE}" \
    --dbname="${DEV_DB}" --exit-on-error "${dump}"

for dir in media private_media; do
    rsync -a --delete --chown=yunnik:lunora-dev "${PROD_APP}/${dir}/" "${DEV_APP}/${dir}/"
done

# Dev code may be ahead of production: apply its migrations, then drop push subscriptions
# (they belong to production's VAPID key and real devices) and dev's cached throttles.
runuser --user yunnik -- bash -c "
    set -Eeuo pipefail
    set -a; source '${DEV_ENV}'; set +a
    cd '${DEV_APP}'
    '${DEV_VENV}/bin/python' manage.py migrate --noinput
    '${DEV_VENV}/bin/python' manage.py shell -c \
        'from app.models import WebPushSubscription; WebPushSubscription.objects.all().delete()'
"
redis-cli -n "${DEV_REDIS_DB}" flushdb >/dev/null

echo "Dev-Daten aus der Produktion übernommen."
