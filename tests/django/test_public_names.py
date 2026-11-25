"""The names isik puts where someone outside the project sees them - each a setting with a default."""

import json
from io import StringIO

import pytest
from django.core.management import call_command

from isik.django import public_names as module
from isik.django.apps.common.middleware.history import HistoryContextMiddleware
from isik.django.conf import isik_areas
from isik.django.public_names import (
    DEAD_SESSION_HEADER,
    PUBLIC_NAMES,
    REQUEST_ID_HEADER,
    SYNC_PROTOCOL_HEADER,
    PublicName,
    public_names,
    public_names_listing,
)


HISTORY = "isik.django.apps.common.middleware.history.HistoryContextMiddleware"
DEAD_SESSIONS = "isik.django.apps.common.middleware.session.DeadSessionCookieMiddleware"


class OwnHistory(HistoryContextMiddleware):
    pass


class TestEach:
    def test_the_three(self):
        assert [(name.setting, name.default) for name in PUBLIC_NAMES] == [
            ("ISIK['DJANGO']['SYNC']['PROTOCOL_HEADER']", "Sync-Protocol"),
            ("ISIK['DJANGO']['HISTORY']['REQUEST_ID_HEADER']", "X-Request-ID"),
            ("ISIK['DJANGO']['SESSIONS']['DEAD_SESSION_HEADER']", "Session-Expired"),
        ]

    def test_isiks_default_until_the_project_sets_one(self, settings):
        assert (SYNC_PROTOCOL_HEADER.value(), SYNC_PROTOCOL_HEADER.overridden()) == ("Sync-Protocol", False)

        isik_areas(settings).SYNC = {"PROTOCOL_HEADER": "Acme-Sync"}

        assert (SYNC_PROTOCOL_HEADER.value(), SYNC_PROTOCOL_HEADER.overridden()) == ("Acme-Sync", True)

    def test_set_to_the_default_is_set(self, settings):
        isik_areas(settings).SYNC = {"PROTOCOL_HEADER": "Sync-Protocol"}

        assert SYNC_PROTOCOL_HEADER.overridden() is True

    def test_an_area_that_isnt_a_dict_sets_nothing(self, settings):
        isik_areas(settings).SESSIONS = "nonsense"

        assert (DEAD_SESSION_HEADER.overridden(), DEAD_SESSION_HEADER.value()) == (False, "Session-Expired")

    def test_in_use_where_its_app_is_installed(self):
        assert SYNC_PROTOCOL_HEADER.in_use() is True
        assert module._app_installed("isik.django.apps.nothing")() is False

    @pytest.mark.parametrize(
        ("middleware", "used"),
        [
            ([HISTORY], True),
            (["tests.django.test_public_names.OwnHistory"], True),
            (["django.middleware.common.CommonMiddleware"], False),
            ([], False),
        ],
    )
    def test_in_use_where_its_middleware_or_a_subclass_is(self, settings, middleware, used):
        settings.MIDDLEWARE = middleware

        assert REQUEST_ID_HEADER.in_use() is used


class TestTheList:
    def test_only_those_in_use(self, settings):
        settings.MIDDLEWARE = [DEAD_SESSIONS]

        assert public_names() == [SYNC_PROTOCOL_HEADER, DEAD_SESSION_HEADER]
        assert public_names(in_use_only=False) == list(PUBLIC_NAMES)

    def test_as_text(self, settings):
        isik_areas(settings).SYNC = {"PROTOCOL_HEADER": "Acme-Sync"}

        assert public_names_listing([SYNC_PROTOCOL_HEADER, DEAD_SESSION_HEADER], "text").splitlines() == [
            "ISIK['DJANGO']['SYNC']['PROTOCOL_HEADER']          Acme-Sync        set             "
            "the header every sync request and response names its protocol version in",
            "ISIK['DJANGO']['SESSIONS']['DEAD_SESSION_HEADER']  Session-Expired  isik's default  "
            "the header saying a request's session cookie is dead",
        ]

    def test_none(self):
        assert public_names_listing([], "text") == ""

    def test_as_json(self, settings):
        isik_areas(settings).SYNC = {"PROTOCOL_HEADER": "Acme-Sync"}
        out = public_names_listing([SYNC_PROTOCOL_HEADER], "json")

        assert out == json.dumps(json.loads(out), indent=2)
        assert json.loads(out) == {
            "public_names": [
                {
                    "setting": "ISIK['DJANGO']['SYNC']['PROTOCOL_HEADER']",
                    "value": "Acme-Sync",
                    "default": "Sync-Protocol",
                    "overridden": True,
                    "seen_as": "the header every sync request and response names its protocol version in",
                }
            ]
        }


def listed(*args):
    out = StringIO()
    call_command("public_names", *args, stdout=out)
    return [line.split()[0] for line in out.getvalue().splitlines()]


class TestTheCommand:
    def test_those_in_use(self, settings):
        settings.MIDDLEWARE = []

        assert listed() == ["ISIK['DJANGO']['SYNC']['PROTOCOL_HEADER']"]

    def test_every_one(self, settings):
        settings.MIDDLEWARE = []

        assert len(listed("--all")) == 3

    def test_only_the_defaults(self, settings):
        isik_areas(settings).SYNC = {"PROTOCOL_HEADER": "Acme-Sync"}

        assert "ISIK['DJANGO']['SYNC']['PROTOCOL_HEADER']" not in listed("--all", "--defaults")
        assert len(listed("--all", "--defaults")) == 2

    def test_nothing_to_list_writes_nothing(self, settings):
        isik_areas(settings).SYNC = {"PROTOCOL_HEADER": "Acme-Sync"}
        settings.MIDDLEWARE = []
        out = StringIO()

        call_command("public_names", "--defaults", stdout=out)

        assert out.getvalue() == ""

    def test_as_json(self):
        out = StringIO()
        call_command("public_names", "--format", "json", stdout=out)

        assert "public_names" in json.loads(out.getvalue())

    def test_the_options(self):
        from isik.django.apps.common.management.commands import public_names as command

        parser = command.Command().create_parser("manage.py", "public_names")
        actions = {action.dest: action for action in parser._actions}

        assert actions["defaults"].help == "Only those left at isik's default."
        assert actions["all"].help == "Also those of what the project doesn't use - an app not installed."
        assert (actions["format"].choices, actions["format"].default) == (["text", "json"], "text")


def test_a_public_name_is_frozen():
    name = PublicName("A", "B", "c", "d", lambda: True)

    with pytest.raises(AttributeError):
        name.default = "e"
