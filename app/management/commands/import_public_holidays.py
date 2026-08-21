from django.core.management.base import BaseCommand, CommandError

from app.models import VacationYear
from app.services.vacation_planner import import_public_holidays


class Command(BaseCommand):
    help = "Importiert offizielle Feiertage für den Urlaubsplaner."

    def add_arguments(self, parser):
        parser.add_argument("--from-year", type=int, required=True)
        parser.add_argument("--to-year", type=int, required=True)
        parser.add_argument("--subdivision", choices=[choice[0] for choice in VacationYear.SUBDIVISION_CHOICES])

    def handle(self, *args, **options):
        from_year = options["from_year"]
        to_year = options["to_year"]
        if to_year < from_year:
            raise CommandError("--to-year muss größer oder gleich --from-year sein.")

        subdivisions = [options["subdivision"]] if options.get("subdivision") else None
        imported = import_public_holidays(from_year, to_year, subdivisions=subdivisions)
        scope = options.get("subdivision") or "alle Bundesländer"
        self.stdout.write(self.style.SUCCESS(f"{imported} Feiertagseinträge für {scope} importiert."))
