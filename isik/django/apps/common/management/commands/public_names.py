from django.core.management.base import BaseCommand

from isik.django.public_names import public_names, public_names_listing


class Command(BaseCommand):
    help = (
        "Lists every name isik puts where someone outside the project's code sees it - a header on the wire - with "
        "the setting naming it, and whether the project set it or left isik's default."
    )

    def add_arguments(self, parser):
        parser.add_argument("--defaults", action="store_true", help="Only those left at isik's default.")
        parser.add_argument(
            "--all", action="store_true", help="Also those of what the project doesn't use - an app not installed."
        )
        parser.add_argument("--format", choices=["text", "json"], default="text")

    def handle(self, *args, defaults, all, format, **options):
        out = public_names_listing(chosen_public_names(defaults=defaults, every=all), format)
        if out:
            self.stdout.write(out)


def chosen_public_names(*, defaults, every):
    """The public names asked for: of what the project uses unless `every`, and only isik's defaults if `defaults`."""
    return [name for name in public_names(in_use_only=not every) if not (defaults and name.overridden())]
