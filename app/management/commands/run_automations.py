import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from app.services.scheduled_tasks import run_scheduled_tasks


class Command(BaseCommand):
    help = "Führt Kalender-Syncs und Benachrichtigungen einmalig oder dauerhaft im Hintergrund aus."

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Läuft dauerhaft und wiederholt die Automatik im konfigurierten Intervall.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=getattr(settings, "LUNORA_AUTOMATION_INTERVAL_SECONDS", 60),
            help="Sekunden zwischen Durchläufen im Loop-Modus.",
        )

    def handle(self, *args, **options):
        interval = options["interval"]
        if interval < 15:
            raise CommandError("Das Automatikintervall muss mindestens 15 Sekunden betragen.")

        while True:
            close_old_connections()
            result = run_scheduled_tasks()
            self.stdout.write(self._summary(result))
            close_old_connections()

            if not options["loop"]:
                return

            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING("Automatik beendet."))
                return

    def _summary(self, result):
        sync = result["calendar_sync"]
        reminders = result["reminder_emails"]
        weekly = result["weekly_summaries"]
        web_push = result.get("web_push", {"sent": 0, "failed": 0})
        return (
            f"Kalender: {sync['synced']} synchronisiert, {sync['failed']} fehlgeschlagen, "
            f"{sync['skipped']} übersprungen | Erinnerungen: {reminders['sent']} gesendet, "
            f"{reminders['failed']} fehlgeschlagen | Wochenberichte: {weekly['sent']} gesendet, "
            f"{weekly['failed']} fehlgeschlagen | Web Push: {web_push['sent']} gesendet, "
            f"{web_push['failed']} fehlgeschlagen"
        )
