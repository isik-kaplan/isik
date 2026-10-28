"""The email senders: plain text left unescaped, the recipient's language, MJML, and a template that isn't there."""

from types import SimpleNamespace

import pytest
from django.core import mail
from django.template import TemplateDoesNotExist
from django.utils import translation

from isik.django.apps.notifications import pipeline
from isik.django.apps.notifications.audience.helpers import subscribe
from isik.django.apps.notifications.channels import email as email_module
from isik.django.apps.notifications.channels.email import language_of
from isik.django.apps.notifications.kinds import raise_notice
from isik.django.apps.notifications.models import Delivery, DeliveryAttempt, DeliveryState
from tests.django.apps.notifications.test_pipeline import fan_out


pytestmark = pytest.mark.django_db

NAME = "Bob's CI & <deploy>"


@pytest.fixture
def reminded(alice, dispatched, django_capture_on_commit_callbacks):
    """An email delivery of a reminder about NAME, to alice."""
    subscribe(alice, "parcel_reminder")
    notice = raise_notice("parcel_reminder", variant="daily", snapshot={"name": NAME})
    fan_out(django_capture_on_commit_callbacks)
    return Delivery.objects.get(notice=notice, channel="email")


class TestPlainTextIsLeftAlone:
    def test_the_subject_and_body_unescaped_and_the_html_escaped(self, reminded):
        assert pipeline.send(reminded.pk).state == DeliveryState.SENT

        [message] = mail.outbox
        assert message.subject == f"Reminder for {NAME}"
        assert message.body == f"{NAME} in en-us\n"
        assert message.alternatives[0].content == "<p>Bob&#x27;s CI &amp; &lt;deploy&gt;</p>\n"


class TestTheRecipientsLanguage:
    def test_their_own_when_they_have_one(self, reminded, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECIPIENT_LANGUAGE": lambda user: "de"}

        pipeline.send(reminded.pk)

        assert mail.outbox[0].body == f"{NAME} in de\n"

    def test_the_sites_when_they_have_none(self, reminded, settings):
        asked = []
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "RECIPIENT_LANGUAGE": lambda user: asked.append(user.username) or None,
        }

        pipeline.send(reminded.pk)

        assert asked == ["alice"]
        assert mail.outbox[0].body == f"{NAME} in en-us\n"

    def test_an_address_belonging_to_nobody_is_written_in_the_sites(self, reminded, settings):
        asked = []
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECIPIENT_LANGUAGE": asked.append}
        reminded.email.user_id = None
        reminded.email.save()

        pipeline.send(reminded.pk)

        assert asked == []

    def test_a_projects_own_sender_is_in_it_too(self, reminded, settings):
        seen = []
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "RECIPIENT_LANGUAGE": lambda user: "de",
            "EMAIL_SENDER": lambda delivery, notice, address: seen.append((translation.get_language(), address)),
        }

        assert pipeline.send(reminded.pk).state == DeliveryState.SENT
        assert seen == [("de", "alice@example.com")]
        assert translation.get_language() == "en-us"

    @pytest.mark.parametrize(
        ("user", "language"),
        [
            (SimpleNamespace(language="de"), "de"),
            (SimpleNamespace(language=""), None),
            (SimpleNamespace(language=None), None),
            (SimpleNamespace(), None),
        ],
    )
    def test_the_default_reads_a_language_only_when_one_is_set(self, user, language):
        assert language_of(user) == language


class TestTheSharedContext:
    def test_every_email_starts_from_it_and_the_kinds_own_wins(self, reminded, settings, monkeypatch):
        asked, rendered = [], []
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "EMAIL_CONTEXT": lambda delivery, notice: (
                asked.append((delivery.pk, notice.pk)) or {"site_name": "Acme", "data": "shadowed"}
            ),
        }
        original = email_module.plain
        monkeypatch.setattr(
            email_module, "plain", lambda name, context: rendered.append(context) or original(name, context)
        )

        pipeline.send(reminded.pk)

        assert asked == [(reminded.pk, reminded.notice_id)]
        assert {(context["site_name"], context["data"]["name"]) for context in rendered} == {("Acme", NAME)}


class TestMjml:
    def test_its_html_from_the_mjml_template(self, reminded, settings):
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "EMAIL_SENDER": "isik.django.apps.notifications.channels.email.send_from_mjml_templates",
        }

        pipeline.send(reminded.pk)

        [message] = mail.outbox
        assert message.subject == f"Reminder for {NAME}"
        html = message.alternatives[0].content
        assert html.startswith("<!doctype html>")
        assert "Bob&#x27;s CI &amp; &lt;deploy&gt;" in html


class TestATemplateThatIsntThere:
    def test_fails_the_delivery_for_good(self, monkeypatch, reminded, settings):
        def missing(name, context):
            raise TemplateDoesNotExist(name)

        monkeypatch.setattr(email_module, "plain", missing)

        failed = pipeline.send(reminded.pk)

        assert failed.state == DeliveryState.ABANDONED
        attempt = DeliveryAttempt.objects.get()
        assert attempt.error == "No template notifications/parcel_reminder/subject.txt."
        assert attempt.request == {"to": "alice@example.com"}
