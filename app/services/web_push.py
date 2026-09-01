import hashlib
import json
import logging
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from pywebpush import WebPushException, webpush

from app.models import UserNotification, WebPushDelivery, WebPushSubscription
from app.services.notification_preferences import (
    CHANNEL_WEB_PUSH,
    notification_channel_enabled,
    notification_preference_map,
    web_push_is_quiet_for_user,
)

logger = logging.getLogger(__name__)

BASE64URL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
EXPIRED_SUBSCRIPTION_STATUS_CODES = {404, 410}


class WebPushTestError(Exception):
    def __init__(self, message, *, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def web_push_configured():
    return bool(settings.WEB_PUSH_ENABLED)


def _trusted_endpoint_host(hostname):
    host = (hostname or "").lower().rstrip(".")
    return any(
        host == allowed_host.lower().rstrip(".") or host.endswith(f".{allowed_host.lower().rstrip('.')}")
        for allowed_host in settings.WEB_PUSH_ALLOWED_ENDPOINT_HOSTS
    )


def validate_web_push_subscription(payload):
    if not isinstance(payload, dict):
        raise ValueError("Ungültige Push-Daten.")

    endpoint = payload.get("endpoint")
    keys = payload.get("keys")
    if not isinstance(endpoint, str) or not isinstance(keys, dict):
        raise ValueError("Push-Endpunkt und Schlüssel fehlen.")

    endpoint = endpoint.strip()
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not isinstance(p256dh, str) or not isinstance(auth, str):
        raise ValueError("Die Push-Schlüssel sind unvollständig.")
    p256dh = p256dh.strip()
    auth = auth.strip()

    parsed = urlsplit(endpoint)
    try:
        endpoint_port = parsed.port
    except ValueError as error:
        raise ValueError("Dieser Push-Endpunkt ist nicht zulässig.") from error
    if (
        len(endpoint) > 2048
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or endpoint_port not in {None, 443}
        or not _trusted_endpoint_host(parsed.hostname)
    ):
        raise ValueError("Dieser Push-Endpunkt ist nicht zulässig.")
    if not (20 <= len(p256dh) <= 512) or not BASE64URL_PATTERN.fullmatch(p256dh):
        raise ValueError("Der öffentliche Push-Schlüssel ist ungültig.")
    if not (8 <= len(auth) <= 256) or not BASE64URL_PATTERN.fullmatch(auth):
        raise ValueError("Der Push-Authentifizierungsschlüssel ist ungültig.")

    return {"endpoint": endpoint, "p256dh": p256dh, "auth": auth}


def register_web_push_subscription(user, payload, *, user_agent=""):
    values = validate_web_push_subscription(payload)
    endpoint_hash = hashlib.sha256(values["endpoint"].encode("utf-8")).hexdigest()
    with transaction.atomic():
        subscription, created = WebPushSubscription.objects.update_or_create(
            endpoint_hash=endpoint_hash,
            defaults={
                "user": user,
                "endpoint": values["endpoint"],
                "p256dh": values["p256dh"],
                "auth": values["auth"],
                "user_agent": (user_agent or "")[:500],
                "failure_count": 0,
            },
        )
    return subscription, created


def remove_web_push_subscription(user, endpoint):
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError("Der Push-Endpunkt fehlt.")
    endpoint_hash = hashlib.sha256(endpoint.strip().encode("utf-8")).hexdigest()
    deleted, _details = WebPushSubscription.objects.filter(
        user=user,
        endpoint_hash=endpoint_hash,
    ).delete()
    return bool(deleted)


def queue_web_push_deliveries(*, per_subscription_limit=50):
    if not web_push_configured():
        return 0

    queued = 0
    subscriptions = list(
        WebPushSubscription.objects.filter(
            user__is_active=True,
            user__profile__notify_desktop=True,
        )
        .select_related("user", "user__profile")
        .order_by("id")
    )
    preferences = notification_preference_map(subscription.user_id for subscription in subscriptions)
    for subscription in subscriptions:
        notifications = list(
            UserNotification.objects.filter(
                recipient_id=subscription.user_id,
                read_at__isnull=True,
                created_at__gte=subscription.created_at,
            )
            .exclude(web_push_deliveries__subscription=subscription)
            .order_by("created_at", "id")[:per_subscription_limit]
        )
        notifications = [
            notification
            for notification in notifications
            if notification_channel_enabled(
                subscription.user,
                notification.kind,
                CHANNEL_WEB_PUSH,
                preference_map=preferences,
            )
        ]
        if not notifications:
            continue
        deliveries = [
            WebPushDelivery(subscription=subscription, notification=notification)
            for notification in notifications
        ]
        WebPushDelivery.objects.bulk_create(deliveries, ignore_conflicts=True)
        queued += len(deliveries)
    return queued


def materialize_web_push_weather_alerts(*, now=None):
    """Materialize weather events needed by either e-mail or Web Push.

    The legacy function name is kept because the automation service and callers
    already use it, but detection is deliberately channel-independent now.
    """
    from app.services.notifications import materialize_scheduled_weather_alerts

    return materialize_scheduled_weather_alerts(now=now)


def _notification_payload(notification):
    target_url = notification.url if notification.url.startswith("/") else "/notifications/"
    return json.dumps(
        {
            "title": notification.title,
            "body": notification.body or "Du hast eine neue Benachrichtigung in Lunora.",
            "url": target_url,
            "tag": f"lunora-notification-{notification.pk}",
            "notificationId": notification.pk,
        },
        ensure_ascii=False,
    )


def _delivery_status_code(error):
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def _send_payload(subscription, payload):
    return webpush(
        subscription_info={
            "endpoint": subscription.endpoint,
            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
        },
        data=json.dumps(payload, ensure_ascii=False),
        vapid_private_key=settings.WEB_PUSH_VAPID_PRIVATE_KEY,
        vapid_claims={"sub": settings.WEB_PUSH_VAPID_SUBJECT},
        ttl=settings.WEB_PUSH_TTL_SECONDS,
        timeout=settings.WEB_PUSH_TIMEOUT_SECONDS,
    )


def send_test_web_push(user, endpoint, *, now=None):
    if not web_push_configured():
        raise WebPushTestError("Web Push ist auf dem Server nicht eingerichtet.", status_code=503)
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise WebPushTestError("Dieses Gerät ist nicht registriert.", status_code=400)

    endpoint_hash = hashlib.sha256(endpoint.strip().encode("utf-8")).hexdigest()
    subscription = WebPushSubscription.objects.filter(
        user=user,
        endpoint_hash=endpoint_hash,
    ).first()
    if subscription is None:
        raise WebPushTestError("Dieses Gerät ist nicht registriert.", status_code=404)

    try:
        _send_payload(
            subscription,
            {
                "title": "Lunora-Testbenachrichtigung",
                "body": "Web Push funktioniert auf diesem Gerät.",
                "url": "/notifications/",
                "tag": "lunora-web-push-test",
            },
        )
    except WebPushException as error:
        status_code = _delivery_status_code(error)
        if status_code in EXPIRED_SUBSCRIPTION_STATUS_CODES:
            subscription.delete()
            raise WebPushTestError(
                "Das Browser-Abonnement ist abgelaufen. Aktiviere dieses Gerät erneut.",
                status_code=410,
            ) from error
        raise WebPushTestError("Die Testbenachrichtigung konnte nicht zugestellt werden.") from error
    except Exception as error:
        raise WebPushTestError("Die Testbenachrichtigung konnte nicht zugestellt werden.") from error

    WebPushSubscription.objects.filter(pk=subscription.pk).update(
        last_success_at=now or timezone.now(),
        failure_count=0,
    )


def send_pending_web_push_notifications(*, now=None, limit=100):
    if not web_push_configured():
        return {
            "sent": 0,
            "failed": 0,
            "removed": 0,
            "queued": 0,
            "deferred": 0,
            "disabled": True,
        }

    current_time = now or timezone.now()
    WebPushDelivery.objects.filter(delivered_at__isnull=True).filter(
        Q(notification__read_at__isnull=False)
        | Q(subscription__user__is_active=False)
        | Q(subscription__user__profile__notify_desktop=False)
    ).delete()
    queued = queue_web_push_deliveries()
    deliveries = list(
        WebPushDelivery.objects.filter(
            delivered_at__isnull=True,
            attempt_count__lt=settings.WEB_PUSH_MAX_ATTEMPTS,
            subscription__user__is_active=True,
            subscription__user__profile__notify_desktop=True,
            notification__read_at__isnull=True,
        )
        .select_related(
            "subscription",
            "subscription__user",
            "subscription__user__profile",
            "notification",
        )
        .order_by("created_at", "id")[:limit]
    )

    sent = 0
    failed = 0
    removed = 0
    deferred = 0
    preference_cache = notification_preference_map(delivery.subscription.user_id for delivery in deliveries)
    for delivery in deliveries:
        subscription = delivery.subscription
        if not notification_channel_enabled(
            subscription.user,
            delivery.notification.kind,
            CHANNEL_WEB_PUSH,
            preference_map=preference_cache,
        ):
            continue
        if web_push_is_quiet_for_user(subscription.user, now=current_time):
            deferred += 1
            continue
        status_code = None
        try:
            _send_payload(subscription, json.loads(_notification_payload(delivery.notification)))
        except WebPushException as error:
            status_code = _delivery_status_code(error)
            if status_code in EXPIRED_SUBSCRIPTION_STATUS_CODES:
                subscription_id = subscription.pk
                subscription.delete()
                removed += 1
                logger.info("Removed expired web push subscription %s", subscription_id)
                continue
            failed += 1
            logger.warning(
                "Web push delivery failed for subscription %s and notification %s with status %s",
                subscription.pk,
                delivery.notification_id,
                status_code or "unknown",
            )
        except Exception:
            failed += 1
            logger.error(
                "Unexpected web push delivery failure for subscription %s and notification %s",
                subscription.pk,
                delivery.notification_id,
            )
        else:
            WebPushDelivery.objects.filter(pk=delivery.pk).update(
                attempt_count=delivery.attempt_count + 1,
                attempted_at=current_time,
                delivered_at=current_time,
                last_status_code=None,
            )
            WebPushSubscription.objects.filter(pk=subscription.pk).update(
                last_success_at=current_time,
                failure_count=0,
            )
            sent += 1
            continue

        WebPushDelivery.objects.filter(pk=delivery.pk).update(
            attempt_count=delivery.attempt_count + 1,
            attempted_at=current_time,
            last_status_code=status_code,
        )
        WebPushSubscription.objects.filter(pk=subscription.pk).update(
            failure_count=F("failure_count") + 1,
        )

    return {
        "sent": sent,
        "failed": failed,
        "removed": removed,
        "queued": queued,
        "deferred": deferred,
    }
