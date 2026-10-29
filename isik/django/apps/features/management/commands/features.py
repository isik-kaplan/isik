from django.core.management.base import BaseCommand

from isik._internal.translation import gettext as _
from isik.django.apps.features.operations import sweep_boundaries


class Command(BaseCommand):
    help = "--sweep tells what grants starting or ending since the last sweep moved - for a cron, with no beat."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sweep",
            action="store_true",
            help="Tell what grants starting or ending since the last sweep moved.",
        )

    def handle(self, *args, sweep, **options):
        if sweep:
            self.stdout.write(_("Told %(count)s boundaries.") % {"count": sweep_boundaries()})
