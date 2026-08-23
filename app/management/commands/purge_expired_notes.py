from django.core.management.base import BaseCommand

from app.services.notes import purge_expired_notes


class Command(BaseCommand):
    help = "Löscht Notizen endgültig, die seit mindestens 30 Tagen im Papierkorb liegen."

    def handle(self, *args, **options):
        deleted = purge_expired_notes()
        self.stdout.write(self.style.SUCCESS(f"{deleted} abgelaufene Notiz(en) endgültig gelöscht."))
