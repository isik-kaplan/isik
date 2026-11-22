"""
Django's `makemessages`, reading MJML templates too: with no `--extension`, the django domain's files are `html`,
`txt`, `py` and `mjml` - Django's own, and the mail templates rendered through MJML (`mjml_template`,
`send_from_mjml_templates`) - so their `{% translate %}` strings reach the catalog. `--extension` names the files
itself, as in Django's; the djangojs domain is Django's own.
"""

from django.core.management.commands import makemessages


# The django domain's files when no --extension names them: Django's own, and MJML templates.
EXTENSIONS = ["html", "txt", "py", "mjml"]


class Command(makemessages.Command):
    def handle(self, **options):
        # makemessages takes no positional arguments - its options are all it's handed.
        if options["domain"] == "django" and not options["extensions"]:
            options["extensions"] = EXTENSIONS
        return super().handle(**options)
