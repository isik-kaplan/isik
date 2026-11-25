"""
`isik` - isik's own commands, for any project using it (`pip install isik[cli]`):

    isik exemptions --import billing.policies --path src/billing    # a project with no Django
    isik exemptions                                                 # DJANGO_SETTINGS_MODULE set: manage.py's
    isik check public_names --defaults

A command over a Django project loads Django first, as `django-admin` does: from `DJANGO_SETTINGS_MODULE`, or
`--settings`, with the working directory importable.
"""

import importlib
import os
import sys
from pathlib import Path

import click

from isik._internal.translation import gettext as _
from isik.common.utils.exemption_listing import UnknownRule, exemption_rules, report, typed_exemptions
from isik.common.utils.exemptions import unimported_exemption_types, unseen_exemption_calls


SETTINGS = "DJANGO_SETTINGS_MODULE"
FORMATS = click.Choice(["text", "json"])


def importable_here():
    """Puts the working directory first on the path, as `manage.py` has its own - so the project's modules are found
    there, before a package of the same name installed elsewhere."""
    # First, even when it's on the path already further down - an editable install puts it there, after
    # site-packages.
    if sys.path[:1] != [os.getcwd()]:
        sys.path.insert(0, os.getcwd())


# Before anything reads the settings module - translating this module's help texts may.
importable_here()


def load_django(settings):
    """Sets Django up for `settings`, else `DJANGO_SETTINGS_MODULE` - False, untouched, when neither names any."""
    if settings:
        os.environ[SETTINGS] = settings
    if not os.environ.get(SETTINGS):
        return False
    import django

    django.setup()
    return True


def package_path(module):
    """Where `module`'s top-level package is - what an exemption scan reads when no `--path` names it."""
    top = importlib.import_module(module.__name__.partition(".")[0])
    return str(Path(top.__file__).parent)


def plain_report(imports, paths, include_library, **options):
    """`report()` of the exemptions `imports` make, every call and type under `paths` held to having been loaded."""
    loaded = [importlib.import_module(name) for name in imports]
    scanned = sorted(set(paths) or {package_path(module) for module in loaded})
    return report(
        lambda: typed_exemptions(include_library=include_library),
        lambda: exemption_rules(typed_exemptions(include_library=include_library)),
        lambda: unseen_exemption_calls(scanned),
        lambda: unimported_exemption_types(scanned),
        **options,
    )


def emit(out, problems, failure):
    if out:
        click.echo(out)
    for problem in problems:
        click.echo(problem, err=True)
    if failure is not None:
        raise click.ClickException(failure)


@click.group(help=_("isik's own commands."))
def isik():
    pass


@isik.command(help=_("Every exemption the project declares, by the rule it skips, with where and why."))
@click.option("--rule", help=_("Only this rule's exemptions."))
@click.option("--rules", is_flag=True, help=_("Each rule, why it's there, and its count."))
@click.option("--format", "format_", type=FORMATS, default="text", show_default=True)
@click.option("--include-library", is_flag=True, help=_("Also the exemptions isik and other libraries make."))
@click.option("--import", "imports", multiple=True, help=_("A module to import, making its exemptions - repeatable."))
@click.option("--path", "paths", multiple=True, help=_("Where the project's code is, for the scan - repeatable."))
@click.option("--urlconf", "urlconfs", multiple=True, help=_("A Django urlconf to load views from - repeatable."))
@click.option("--settings", help=_("The Django settings module, over DJANGO_SETTINGS_MODULE."))
def exemptions(rule, rules, format_, include_library, imports, paths, urlconfs, settings):
    options = {"rule": rule, "rules_only": rules, "format": format_}
    try:
        if load_django(settings):
            from isik.django.apps.common.management.commands.exemptions import django_report

            emit(*django_report(list(urlconfs) or None, include_library, **options))
        else:
            emit(*plain_report(imports, paths, include_library, **options))
    except UnknownRule as unknown:
        raise click.ClickException(str(unknown)) from None


@isik.group(help=_("What a project left to configure."))
def check():
    pass


@check.command("public_names", help=_("Every name isik puts where someone outside the project sees it."))
@click.option("--defaults", is_flag=True, help=_("Only those left at isik's default."))
@click.option("--all", "every", is_flag=True, help=_("Also those of what the project doesn't use."))
@click.option("--format", "format_", type=FORMATS, default="text", show_default=True)
@click.option("--settings", help=_("The Django settings module, over DJANGO_SETTINGS_MODULE."))
def public_names(defaults, every, format_, settings):
    if not load_django(settings):
        raise click.ClickException(
            _("isik check public_names reads a Django project's settings - set %(setting)s.") % {"setting": SETTINGS}
        )
    from isik.django.apps.common.management.commands.public_names import chosen_public_names
    from isik.django.public_names import public_names_listing

    emit(public_names_listing(chosen_public_names(defaults=defaults, every=every), format_), [], None)
