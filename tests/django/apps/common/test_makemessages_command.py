"""`manage.py makemessages` - Django's, its django domain reading MJML templates too when no extension is named."""

import pytest
from django.core.management import call_command
from django.core.management.commands import makemessages


@pytest.fixture
def handled(monkeypatch):
    """The options Django's own makemessages is handed - instead of it running."""
    seen = []
    monkeypatch.setattr(makemessages.Command, "handle", lambda self, *args, **options: seen.append(options))
    return seen


def test_the_django_domain_reads_mjml_too(handled):
    call_command("makemessages", locale=["de"])

    [options] = handled
    assert options["extensions"] == ["html", "txt", "py", "mjml"]


def test_extensions_named_are_kept(handled):
    call_command("makemessages", locale=["de"], extensions=["html"])

    assert handled[0]["extensions"] == ["html"]


def test_the_djangojs_domain_is_djangos_own(handled):
    call_command("makemessages", locale=["de"], domain="djangojs")

    assert handled[0]["extensions"] is None
