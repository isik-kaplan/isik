"""segments(), the SMS checks (isik.E024, isik.W010), and the pytest plugin's SMS fixtures."""

import pytest

from isik import pytest_plugin
from isik.django import sms
from isik.django.apps.common.checks import sms_senders_are_configured
from isik.django.conf import django_area, isik_areas
from isik.django.sms import Segments, segments
from isik.django.sms.backends import locmem
from isik.django.sms.segments import GSM_BASIC, GSM_EXTENSION


TWILIO = "isik.django.sms.backends.twilio.SMSBackend"
CONSOLE = "isik.django.sms.backends.console.SMSBackend"
TWILIO_OPTIONS = {"account_sid": "AC", "auth_token": "t", "from_": "+1"}


class TestSegments:
    @pytest.mark.parametrize(
        ("body", "answer"),
        [
            ("", Segments("GSM-7", 0, 1)),
            ("Your code is 123456", Segments("GSM-7", 19, 1)),
            ("a" * 160, Segments("GSM-7", 160, 1)),
            ("a" * 161, Segments("GSM-7", 161, 2)),
            ("a" * 306, Segments("GSM-7", 306, 2)),
            ("a" * 307, Segments("GSM-7", 307, 3)),
            ("€" * 80, Segments("GSM-7", 160, 1)),
            ("€" * 81, Segments("GSM-7", 162, 2)),
            ("{}[]~|^\\\f", Segments("GSM-7", 18, 1)),
            ("Ça coûte 5 £", Segments("UCS-2", 12, 1)),
            ("ş" * 70, Segments("UCS-2", 70, 1)),
            ("ş" * 71, Segments("UCS-2", 71, 2)),
            ("ş" * 134, Segments("UCS-2", 134, 2)),
            ("ş" * 135, Segments("UCS-2", 135, 3)),
            ("Kodun 123456 🙂", Segments("UCS-2", 15, 1)),
        ],
    )
    def test_how_a_body_is_sent(self, body, answer):
        assert segments(body) == answer

    def test_units_are_whole(self):
        assert isinstance(segments("ş🙂").units, int)

    def test_the_alphabet(self):
        assert len(GSM_BASIC) == 127
        assert len(GSM_EXTENSION) == 10
        assert not GSM_BASIC & GSM_EXTENSION


class TestTheChecks:
    def run(self, settings, senders, debug=False):
        settings.DEBUG = debug
        isik_areas(settings).SMS = senders
        messages = sms_senders_are_configured(None)
        return [(message.id, message.msg, message.hint) for message in messages]

    def errors(self, settings, senders):
        return [msg for id, msg, hint in self.run(settings, senders, debug=True) if id == "isik.E024"]

    def test_the_test_projects_senders(self):
        assert sms_senders_are_configured(None) == []

    def test_no_sms_set(self, settings):
        del isik_areas(settings).SMS

        assert sms_senders_are_configured(None) == []

    def test_sms_that_isnt_a_dict(self, settings):
        assert self.run(settings, ["default"]) == [
            ("isik.E024", "ISIK['DJANGO']['SMS'] is ['default'], not a dict of senders.", "See isik.django.sms.")
        ]

    def test_a_sender_that_isnt_a_dict(self, settings):
        assert self.errors(settings, {"default": TWILIO}) == [
            f"ISIK['DJANGO']['SMS']['default'] is {TWILIO!r}, not a dict."
        ]

    def test_keys_it_doesnt_know(self, settings):
        assert self.errors(settings, {"default": {"BACKEND": CONSOLE, "OPTION": {}, 1: 2}}) == [
            "ISIK['DJANGO']['SMS']['default'] has keys it doesn't know: 1, 'OPTION' - it holds BACKEND and OPTIONS."
        ]

    @pytest.mark.parametrize("config", [{}, {"BACKEND": None}, {"OPTIONS": {}}])
    def test_no_backend(self, settings, config):
        assert self.errors(settings, {"default": config}) == [
            "ISIK['DJANGO']['SMS']['default'] names no BACKEND - the dotted path of its sender."
        ]

    def test_options_that_arent_a_dict(self, settings):
        assert self.errors(settings, {"default": {"BACKEND": CONSOLE, "OPTIONS": ["from_"]}}) == [
            "ISIK['DJANGO']['SMS']['default']'s OPTIONS is ['from_'], not a dict."
        ]

    def test_a_backend_that_isnt_one(self, settings):
        assert self.errors(settings, {"default": {"BACKEND": "isik.django.sms.message.SMSMessage"}}) == [
            "ISIK['DJANGO']['SMS']['default']: 'isik.django.sms.message.SMSMessage' isn't a BaseSMSBackend."
        ]

    def test_an_option_it_doesnt_take(self, settings):
        assert self.errors(
            settings, {"default": {"BACKEND": CONSOLE, "OPTIONS": {"from_": "x", "region": "ie1", "api_key": "k"}}}
        ) == ["ISIK['DJANGO']['SMS']['default']'s OPTIONS has api_key, region, which SMSBackend doesn't take."]

    def test_what_its_own_check_refuses(self, settings):
        assert self.errors(
            settings, {"us": {"BACKEND": TWILIO, "OPTIONS": {"account_sid": "AC", "auth_token": "t"}}}
        ) == ["ISIK['DJANGO']['SMS']['us']: twilio.SMSBackend sends from from_, or a messaging_service_sid - set one."]

    def test_every_sender(self, settings):
        assert self.errors(
            settings,
            {"default": {"BACKEND": TWILIO, "OPTIONS": TWILIO_OPTIONS}, "other": {}, "third": {"BACKEND": CONSOLE}},
        ) == ["ISIK['DJANGO']['SMS']['other'] names no BACKEND - the dotted path of its sender."]

    def test_a_sender_configured_right(self, settings):
        assert self.run(settings, {"default": {"BACKEND": TWILIO, "OPTIONS": TWILIO_OPTIONS}}) == []

    @pytest.mark.parametrize("backend", ["console", "dummy"])
    def test_one_sending_nowhere_with_debug_off(self, settings, backend):
        path = f"isik.django.sms.backends.{backend}.SMSBackend"

        assert self.run(settings, {"default": {"BACKEND": path}}) == [
            (
                "isik.W010",
                f"ISIK['DJANGO']['SMS']['default'] is {path}, which sends no SMS anywhere - and DEBUG is off.",
                "Name a sender that sends, or silence isik.W010 where that's meant.",
            )
        ]

    def test_the_file_one_with_debug_off(self, settings, tmp_path):
        path = "isik.django.sms.backends.filebased.SMSBackend"

        assert [
            id
            for id, msg, hint in self.run(
                settings, {"default": {"BACKEND": path, "OPTIONS": {"file_path": str(tmp_path)}}}
            )
        ] == ["isik.W010"]

    def test_locmem_takes_a_real_senders_options_and_isnt_warned_of(self, settings):
        assert (
            self.run(
                settings,
                {"default": {"BACKEND": "isik.django.sms.backends.locmem.SMSBackend", "OPTIONS": TWILIO_OPTIONS}},
            )
            == []
        )

    def test_one_sending_nowhere_with_debug_on(self, settings):
        assert self.run(settings, {"default": {"BACKEND": CONSOLE}}, debug=True) == []

    def test_one_configured_wrong_isnt_warned_of_too(self, settings):
        assert [id for id, msg, hint in self.run(settings, {"default": {"BACKEND": CONSOLE, "OPTIONS": []}})] == [
            "isik.E024"
        ]


class TestThePlugin:
    def test_every_sender_sends_to_the_outbox_for_the_run(self):
        assert {alias: config["BACKEND"] for alias, config in django_area("SMS").items()} == {
            "default": "isik.django.sms.backends.locmem.SMSBackend"
        }
        assert django_area("SMS")["default"]["OPTIONS"]["from_"] == "+14155550100"

    def test_each_sender_made_locmem_for_the_session(self, settings):
        isik_areas(settings).SMS = {
            "default": {"BACKEND": TWILIO, "OPTIONS": TWILIO_OPTIONS},
            "broken": "not a dict",
        }
        generator = pytest_plugin._isik_sms_to_outbox.__wrapped__()

        next(generator)
        during = django_area("SMS")
        next(generator, None)

        assert during == {
            "default": {"BACKEND": "isik.django.sms.backends.locmem.SMSBackend", "OPTIONS": TWILIO_OPTIONS},
            "broken": "not a dict",
        }
        assert django_area("SMS")["default"]["BACKEND"] == TWILIO

    def test_nothing_changes_without_senders(self, monkeypatch):
        monkeypatch.setattr(pytest_plugin, "sms_configured", lambda: False)
        before = django_area("SMS")
        generator = pytest_plugin._isik_sms_to_outbox.__wrapped__()

        next(generator)
        assert django_area("SMS") is before
        next(generator, None)

    def test_the_outbox_emptied_before_each_test(self):
        sms.outbox.append("left over")
        locmem.respond("+1", "x")
        generator = pytest_plugin._isik_sms_outbox.__wrapped__(None)

        next(generator)

        assert (sms.outbox, locmem.responses) == ([], {})
        next(generator, None)

    def test_left_alone_without_senders(self, monkeypatch):
        monkeypatch.setattr(pytest_plugin, "sms_configured", lambda: False)
        sms.outbox.append("left over")
        generator = pytest_plugin._isik_sms_outbox.__wrapped__(None)

        next(generator)

        assert sms.outbox == ["left over"]
        next(generator, None)

    def test_configured(self):
        assert pytest_plugin.sms_configured() is True

    @pytest.mark.parametrize("senders", [{}, ["default"]])
    def test_not_configured(self, settings, senders):
        isik_areas(settings).SMS = senders

        assert pytest_plugin.sms_configured() is False

    def test_not_set(self, settings):
        del isik_areas(settings).SMS

        assert pytest_plugin.sms_configured() is False

    def test_not_before_the_apps_are_ready(self, monkeypatch):
        from django.apps import apps

        monkeypatch.setattr(apps, "ready", False)

        assert pytest_plugin.sms_configured() is False

    def test_without_django_it_isnt(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "django.apps", None)

        assert pytest_plugin.sms_configured() is False
