from django.utils import timezone

from app.models import CalendarSource


def queue_calendar_sources(sources):
    """Persist sync requests without performing network I/O in the web request."""
    source_list = list(sources)
    if not source_list:
        return {"queued": 0, "message": "Noch kein Kalender gespeichert."}

    enabled_ids = [source.pk for source in source_list if source.enabled and source.pk]
    if not enabled_ids:
        return {"queued": 0, "message": "Keine aktiven Kalender zum Synchronisieren."}

    requested_at = timezone.now()
    queued_count = CalendarSource.objects.filter(
        pk__in=enabled_ids,
        enabled=True,
    ).update(
        sync_requested_at=requested_at,
        last_error="",
        updated_at=requested_at,
    )

    if queued_count == 1:
        message = "Kalendersynchronisierung wurde im Hintergrund vorgemerkt."
    else:
        message = f"{queued_count} Kalendersynchronisierungen wurden im Hintergrund vorgemerkt."
    return {"queued": queued_count, "message": message}
