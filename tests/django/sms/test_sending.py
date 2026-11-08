"""Sending: SMSMessage, send_sms(), senders, and the way every sender sends - BaseSMSBackend."""

import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from django.test import override_settings

from isik.django import sms
from isik.django.conf import isik_areas
from isik.django.sms import (
    InvalidSender,
    RecipientStatus,
    SenderDoesNotExist,
    SendStatus,
    SMSAPIError,
    SMSCancelSend,
    SMSInvalidNumber,
    SMSMessage,
    SMSRateLimited,
    SMSRecipientsRefused,
    SMSUnsupportedFeature,
    Status,
    send_sms,
    senders,
    signals,
)
from isik.django.sms.backends import base, console, dummy, filebased, locmem
from isik.django.sms.backends.locmem import respond
from isik.django.sms.message import aware, checked_number


NUMBER = "+14155550123"
OTHER = "+447700900123"
CONSOLE = "isik.django.sms.backends.console.SMSBackend"
LOCMEM = "isik.django.sms.backends.locmem.SMSBackend"
DUMMY = "isik.django.sms.backends.dummy.SMSBackend"


class TestSendSms:
    def test_sends_through_the_default_sender(self):
        status = send_sms(NUMBER, "Your code is 123456")

        (message,) = sms.outbox
        assert (message.to, message.body, message.sent_using) == ([NUMBER], "Your code is 123456", "default")
        assert status == message.send_status
        assert status.recipients[NUMBER].status is Status.QUEUED

    def test_the_test_runs_default_sender_is_locmem_with_its_own_options(self):
        sender = senders.default

        assert isinstance(sender, locmem.SMSBackend)
        assert sender.from_ == "+14155550100"

    def test_sends_to_each_number(self):
        status = send_sms([NUMBER, OTHER], "Doors open at 7.")

        assert list(status.recipients) == [NUMBER, OTHER]
        assert len(status.message_ids) == 2

    def test_through_another_sender(self, senders_configured):
        senders_configured({"default": {"BACKEND": DUMMY}, "loud": {"BACKEND": LOCMEM}})

        send_sms(NUMBER, "Hi", using="loud")

        assert sms.outbox[0].sent_using == "loud"

    def test_fields_reach_the_message(self):
        send_sms(NUMBER, "Hi", "+14155550199", validity=60, metadata={"invite": "1"})

        (message,) = sms.outbox
        assert (message.from_, message.validity, message.metadata) == ("+14155550199", 60, {"invite": "1"})

    def test_a_cancelled_message_answers_none(self):
        def cancel(**kwargs):
            raise SMSCancelSend

        signals.pre_send.connect(cancel)
        try:
            assert send_sms(NUMBER, "Hi") is None
        finally:
            signals.pre_send.disconnect(cancel)
        assert sms.outbox == []


class TestSMSMessage:
    def test_one_number_or_several(self):
        assert SMSMessage(NUMBER, "Hi").to == [NUMBER]
        assert SMSMessage((NUMBER, OTHER), "Hi").to == [NUMBER, OTHER]

    def test_its_defaults(self):
        message = SMSMessage(NUMBER, "Hi")

        assert (message.from_, message.media_urls, message.metadata, message.provider_extra) == (None, [], {}, {})
        assert (message.send_at, message.validity, message.status_callback, message.message_type) == (
            None,
            None,
            None,
            None,
        )
        assert (message.using, message.send_status, message.sent_using) == ("default", None, None)

    def test_copies_what_its_given(self):
        metadata, extra, media = {"a": "1"}, {"SmartEncoded": True}, ["https://x/1.png"]

        message = SMSMessage(NUMBER, "Hi", metadata=metadata, provider_extra=extra, media_urls=media)
        metadata["b"], extra["c"] = "2", 3
        media.append("y")

        assert (message.metadata, message.provider_extra, message.media_urls) == (
            {"a": "1"},
            {"SmartEncoded": True},
            ["https://x/1.png"],
        )

    def test_repr(self):
        assert repr(SMSMessage(NUMBER, "Hi")) == "<SMSMessage to=['+14155550123'] body='Hi'>"

    def test_its_features_are_the_optional_fields_it_sets(self):
        assert SMSMessage(NUMBER, "Hi", provider_extra={"x": 1}, status_callback="https://x").features == set()
        assert SMSMessage(
            NUMBER,
            "Hi",
            media_urls=["u"],
            metadata={"a": "1"},
            send_at=datetime(2030, 1, 1, tzinfo=UTC),
            validity=60,
            message_type="transactional",
        ).features == {"media_urls", "metadata", "send_at", "validity", "message_type"}

    @pytest.mark.parametrize(("validity", "seconds"), [(None, None), (90, 90), (timedelta(minutes=2), 120)])
    def test_validity_in_seconds(self, validity, seconds):
        assert SMSMessage(NUMBER, "Hi", validity=validity).validity_seconds() == seconds

    @override_settings(TIME_ZONE="Europe/Istanbul")
    def test_send_at_is_aware(self):
        assert SMSMessage(NUMBER, "Hi", send_at=datetime(2030, 1, 1, 12)).send_at_aware().utcoffset() == timedelta(
            hours=3
        )
        assert SMSMessage(NUMBER, "Hi").send_at_aware() is None

    def test_aware_keeps_an_aware_moment(self):
        moment = datetime(2030, 1, 1, tzinfo=UTC)

        assert aware(moment) is moment
        assert aware(None) is None

    def test_send_answers_one(self):
        assert SMSMessage(NUMBER, "Hi").send() == 1

    def test_send_goes_through_its_sender(self, senders_configured):
        senders_configured({"default": {"BACKEND": DUMMY}, "loud": {"BACKEND": LOCMEM}})

        SMSMessage(NUMBER, "Hi", using="loud").send()

        assert sms.outbox[0].sent_using == "loud"


class TestNumbers:
    @pytest.mark.parametrize("number", [NUMBER, "+12", "+123456789012345"])
    def test_e164(self, number):
        assert checked_number(number) == number

    @pytest.mark.parametrize("number", ["14155550123", "+0123", "+1", "+1234567890123456", "+1 415", 14155550123])
    def test_not_e164(self, number):
        with pytest.raises(SMSInvalidNumber) as refused:
            checked_number(number)

        assert str(refused.value) == (
            f"{number!r} isn't an E.164 phone number - a + and up to 15 digits, such as +14155550123."
        )

    def test_a_message_to_one_is_refused_before_anything_is_sent(self):
        with pytest.raises(SMSInvalidNumber):
            send_sms([NUMBER, "555"], "Hi")

        assert sms.outbox == []


class TestSendStatus:
    def test_ids_statuses_and_refused(self):
        status = SendStatus(
            {
                NUMBER: RecipientStatus(Status.QUEUED, message_id="a"),
                OTHER: RecipientStatus(Status.INVALID),
            }
        )

        assert (status.message_ids, status.statuses, status.refused) == (
            {"a"},
            {Status.QUEUED, Status.INVALID},
            False,
        )

    def test_refused_by_every_number(self):
        assert SendStatus({NUMBER: RecipientStatus(Status.INVALID), OTHER: RecipientStatus(Status.REJECTED)}).refused

    def test_no_numbers_isnt_refused(self):
        assert not SendStatus().refused


class TestSenders:
    def test_built_afresh_from_its_options(self, senders_configured):
        senders_configured({"default": {"BACKEND": CONSOLE, "OPTIONS": {"from_": "Acme", "fail_silently": True}}})

        first, second = senders["default"], senders.default

        assert first is not second
        assert isinstance(first, console.SMSBackend)
        assert (first.alias, first.from_, first.fail_silently) == ("default", "Acme", True)

    def test_an_alias_not_configured_names_every_one_that_is(self, senders_configured):
        senders_configured({"default": {"BACKEND": DUMMY}, "loud": {"BACKEND": DUMMY}})

        with pytest.raises(SenderDoesNotExist, match=r"it configures 'default', 'loud'\.$"):
            _ = senders["quiet"]

    def test_iterates_the_aliases(self, senders_configured):
        senders_configured({"default": {"BACKEND": DUMMY}, "loud": {"BACKEND": DUMMY}})

        assert list(senders) == ["default", "loud"]

    def test_an_alias_not_configured(self, senders_configured):
        senders_configured({"default": {"BACKEND": DUMMY}})

        with pytest.raises(SenderDoesNotExist) as refused:
            _ = senders["marketing"]

        assert str(refused.value) == (
            "ISIK['DJANGO']['SMS'] configures no sender 'marketing' - it configures 'default'."
        )

    def test_no_senders_configured(self, settings):
        del isik_areas(settings).SMS

        with pytest.raises(SenderDoesNotExist) as refused:
            _ = senders.default

        assert str(refused.value) == "ISIK['DJANGO']['SMS'] configures no sender 'default' - it configures none."

    def test_a_backend_that_cant_be_imported(self, senders_configured):
        senders_configured({"default": {"BACKEND": "isik.nowhere.SMSBackend"}})

        with pytest.raises(InvalidSender, match=r"^'isik.nowhere.SMSBackend' can't be imported: "):
            _ = senders.default

    @pytest.mark.parametrize("path", ["isik.django.sms.message.SMSMessage", "isik.django.sms.message.E164"])
    def test_a_backend_that_isnt_one(self, senders_configured, path):
        senders_configured({"default": {"BACKEND": path}})

        with pytest.raises(InvalidSender) as refused:
            _ = senders.default

        assert str(refused.value) == f"{path!r} isn't a BaseSMSBackend."

    def test_an_option_it_doesnt_take(self, senders_configured):
        senders_configured({"default": {"BACKEND": DUMMY, "OPTIONS": {"api_key": "k", "region": "x"}}})

        with pytest.raises(InvalidSender) as refused:
            _ = senders.default

        assert str(refused.value) == "SMSBackend doesn't take api_key, region."


class Recorder(base.BaseSMSBackend):
    """A sender answering each number as scripted, recording what it was asked."""

    features = frozenset({"validity"})

    def __init__(self, answers=None, **options):
        super().__init__(**options)
        self.answers = answers or {}
        self.sent = []
        self.opened = self.closed = 0

    def open(self):
        self.opened += 1
        return True

    def close(self):
        self.closed += 1

    def send_one(self, message, recipient):
        self.sent.append((message.body, recipient))
        answer = self.answers.get(recipient, Status.QUEUED)
        if isinstance(answer, Exception):
            raise answer
        return RecipientStatus(answer, message_id=recipient)


class TestBaseSMSBackend:
    def test_sends_each_number_of_each_message(self):
        sender = Recorder()

        assert sender.send_messages([SMSMessage([NUMBER, OTHER], "a"), SMSMessage(NUMBER, "b")]) == 2

        assert sender.sent == [("a", NUMBER), ("a", OTHER), ("b", NUMBER)]

    def test_nothing_to_send(self):
        sender = Recorder()

        assert sender.send_messages([]) == 0
        assert sender.opened == 0

    def test_opens_and_closes_around_the_messages(self):
        sender = Recorder()

        sender.send_messages([SMSMessage(NUMBER, "a"), SMSMessage(NUMBER, "b")])

        assert (sender.opened, sender.closed) == (1, 1)

    def test_closes_when_sending_raises(self):
        sender = Recorder({NUMBER: SMSAPIError("down")})

        with pytest.raises(SMSAPIError):
            sender.send_messages([SMSMessage(NUMBER, "a")])

        assert sender.closed == 1

    def test_leaves_open_what_it_didnt_open(self):
        class AlreadyOpen(Recorder):
            def open(self):
                super().open()
                return False

        sender = AlreadyOpen()

        sender.send_messages([SMSMessage(NUMBER, "a")])

        assert sender.closed == 0

    def test_a_context_manager(self):
        with Recorder() as sender:
            assert (sender.opened, sender.closed) == (1, 0)

        assert sender.closed == 1

    def test_a_context_manager_closes_when_opening_fails(self):
        class Broken(Recorder):
            def open(self):
                raise OSError("no")

        sender = Broken()

        with pytest.raises(OSError, match="no"), sender:
            pass

        assert sender.closed == 1

    def test_the_status_and_sender_are_kept_on_the_message(self):
        message = SMSMessage([NUMBER, OTHER], "a")

        Recorder(alias="loud").send_messages([message])

        assert message.sent_using == "loud"
        assert message.send_status == SendStatus(
            {
                NUMBER: RecipientStatus(Status.QUEUED, message_id=NUMBER),
                OTHER: RecipientStatus(Status.QUEUED, message_id=OTHER),
            }
        )

    def test_every_number_refusing_raises(self):
        message = SMSMessage([NUMBER, OTHER], "a")

        with pytest.raises(SMSRecipientsRefused) as refused:
            Recorder({NUMBER: Status.INVALID, OTHER: Status.REJECTED}).send_messages([message])

        assert str(refused.value) == (
            "Every number refused the message: +14155550123 (invalid), +447700900123 (rejected)."
        )
        assert refused.value.status is message.send_status

    def test_some_refusing_doesnt(self):
        assert Recorder({NUMBER: Status.INVALID}).send_messages([SMSMessage([NUMBER, OTHER], "a")]) == 1

    def test_ignore_recipient_status(self):
        sender = Recorder({NUMBER: Status.INVALID}, ignore_recipient_status=True)

        assert sender.send_messages([SMSMessage(NUMBER, "a")]) == 1

    def test_fail_silently_swallows_an_sms_error(self):
        sender = Recorder({NUMBER: SMSAPIError("down")}, fail_silently=True)

        assert sender.send_messages([SMSMessage(NUMBER, "a"), SMSMessage(OTHER, "b")]) == 1

    def test_fail_silently_swallows_nothing_else(self):
        sender = Recorder({NUMBER: RuntimeError("bug")}, fail_silently=True)

        with pytest.raises(RuntimeError):
            sender.send_messages([SMSMessage(NUMBER, "a")])

    def test_a_feature_it_doesnt_have(self):
        with pytest.raises(SMSUnsupportedFeature) as refused:
            Recorder().send_messages([SMSMessage(NUMBER, "a", media_urls=["u"], send_at=datetime(2030, 1, 1))])

        assert str(refused.value) == (
            "Recorder can't send a message's media_urls - set ignore_unsupported_features to send it without."
        )

    def test_a_feature_it_has(self):
        assert Recorder().send_messages([SMSMessage(NUMBER, "a", validity=60)]) == 1

    def test_ignore_unsupported_features(self):
        sender = Recorder(ignore_unsupported_features=True)

        assert sender.send_messages([SMSMessage(NUMBER, "a", media_urls=["u"])]) == 1

    def test_pre_send_may_change_the_message(self):
        def louder(message, **kwargs):
            message.body = message.body.upper()

        sender = Recorder()
        signals.pre_send.connect(louder)
        try:
            sender.send_messages([SMSMessage(NUMBER, "a")])
        finally:
            signals.pre_send.disconnect(louder)

        assert sender.sent == [("A", NUMBER)]

    def test_pre_send_may_cancel_it(self):
        def cancel(**kwargs):
            raise SMSCancelSend

        sender = Recorder()
        signals.pre_send.connect(cancel)
        try:
            assert sender.send_messages([SMSMessage(NUMBER, "a")]) == 0
        finally:
            signals.pre_send.disconnect(cancel)

        assert sender.sent == []

    def test_the_signals_are_sent(self):
        heard = []

        def before(sender, message, sender_alias, **kwargs):
            heard.append(("pre", sender, message.body, sender_alias))

        def after(sender, message, status, sender_alias, **kwargs):
            heard.append(("post", sender, status is message.send_status, sender_alias))

        signals.pre_send.connect(before)
        signals.post_send.connect(after)
        try:
            Recorder(alias="loud").send_messages([SMSMessage(NUMBER, "a")])
        finally:
            signals.pre_send.disconnect(before)
            signals.post_send.disconnect(after)

        assert heard == [("pre", Recorder, "a", "loud"), ("post", Recorder, True, "loud")]

    def test_post_send_comes_before_a_refusal(self):
        heard = []

        def after(message, **kwargs):
            heard.append(message.send_status.refused)

        signals.post_send.connect(after)
        try:
            with pytest.raises(SMSRecipientsRefused):
                Recorder({NUMBER: Status.INVALID}).send_messages([SMSMessage(NUMBER, "a")])
        finally:
            signals.post_send.disconnect(after)

        assert heard == [True]

    def test_the_sender_and_callback_a_message_names_win(self):
        sender = Recorder(from_="Acme", status_callback="https://acme/cb")

        assert (sender.sender_of(SMSMessage(NUMBER, "a")), sender.callback_of(SMSMessage(NUMBER, "a"))) == (
            "Acme",
            "https://acme/cb",
        )
        named = SMSMessage(NUMBER, "a", "+14155550199", status_callback="https://x/cb")
        assert (sender.sender_of(named), sender.callback_of(named)) == ("+14155550199", "https://x/cb")

    def test_its_defaults(self):
        sender = base.BaseSMSBackend()

        assert (sender.alias, sender.fail_silently, sender.from_, sender.status_callback) == (
            "default",
            False,
            None,
            None,
        )
        assert (sender.ignore_unsupported_features, sender.ignore_recipient_status) == (False, False)
        assert (sender.open(), sender.close(), base.BaseSMSBackend.check({})) == (False, None, [])
        assert (base.BaseSMSBackend.options, base.BaseSMSBackend.features, base.BaseSMSBackend.sends_nowhere) == (
            (),
            frozenset(),
            False,
        )
        assert base.BaseSMSBackend.takes_any_option is False

    def test_send_one_is_a_subclasss(self):
        with pytest.raises(NotImplementedError) as refused:
            base.BaseSMSBackend().send_one(SMSMessage(NUMBER, "a"), NUMBER)

        assert str(refused.value) == "A subclass of BaseSMSBackend sends one message to one number - send_one()."


class TestConsole:
    def test_one_line_a_number(self):
        stream = io.StringIO()

        console.SMSBackend(stream=stream, from_="Acme").send_messages([SMSMessage([NUMBER, OTHER], "Hi\nthere\r\\")])

        assert stream.getvalue() == (
            "SMS from Acme to +14155550123: Hi\\nthere\\r\\\\\nSMS from Acme to +447700900123: Hi\\nthere\\r\\\\\n"
        )

    def test_no_sender(self, capsys):
        console.SMSBackend().send_messages([SMSMessage(NUMBER, "Hi")])

        assert capsys.readouterr().out == "SMS from - to +14155550123: Hi\n"

    def test_answers_sent_and_sends_everything(self):
        message = SMSMessage(NUMBER, "Hi", media_urls=["u"], metadata={"a": "1"}, validity=1, message_type="x")

        console.SMSBackend(stream=io.StringIO()).send_messages([message])

        assert message.send_status.recipients[NUMBER] == RecipientStatus(Status.SENT)
        assert console.SMSBackend.sends_nowhere


class TestDummy:
    def test_sends_nowhere(self):
        message = SMSMessage(NUMBER, "Hi", send_at=datetime(2030, 1, 1), message_type="x")

        assert dummy.SMSBackend().send_messages([message]) == 1
        assert message.send_status.recipients[NUMBER] == RecipientStatus(Status.SENT)
        assert dummy.SMSBackend.sends_nowhere


class TestFilebased:
    def test_a_json_line_a_number(self, tmp_path):
        path = tmp_path / "sms" / "e2e" / "sent.jsonl"
        message = SMSMessage(
            [NUMBER, OTHER],
            "Kodun 🙂",
            media_urls=["u"],
            metadata={"a": "1"},
            send_at=datetime(2030, 1, 1, tzinfo=UTC),
            validity=timedelta(minutes=1),
            message_type="transactional",
        )

        filebased.SMSBackend(file_path=path, from_="Acme", alias="files").send_messages([message])

        lines = path.read_text(encoding="utf-8").splitlines()
        assert [json.loads(line) for line in lines] == [
            {
                "to": number,
                "from": "Acme",
                "body": "Kodun 🙂",
                "media_urls": ["u"],
                "metadata": {"a": "1"},
                "send_at": "2030-01-01T00:00:00+00:00",
                "validity": 60,
                "message_type": "transactional",
                "sent_using": "files",
            }
            for number in (NUMBER, OTHER)
        ]
        assert lines[0].isascii()
        assert message.send_status.recipients[NUMBER] == RecipientStatus(Status.SENT)

    def test_appends(self, tmp_path):
        path = tmp_path / "sent.jsonl"
        sender = filebased.SMSBackend(file_path=str(path))

        sender.send_messages([SMSMessage(NUMBER, "a")])
        sender.send_messages([SMSMessage(NUMBER, "b")])

        assert [json.loads(line)["body"] for line in path.read_text().splitlines()] == ["a", "b"]
        assert json.loads(path.read_text().splitlines()[0])["send_at"] is None

    def test_its_check(self):
        assert filebased.SMSBackend.check({"file_path": "x"}) == []
        assert filebased.SMSBackend.check({}) == ["filebased.SMSBackend writes to file_path - set it."]
        assert filebased.SMSBackend.sends_nowhere


class TestLocmem:
    def test_keeps_a_copy(self):
        message = SMSMessage(NUMBER, "Hi", metadata={"a": "1"})

        locmem.SMSBackend(alias="loud").send_messages([message])
        message.metadata["b"] = "2"

        (kept,) = sms.outbox
        assert kept is not message
        assert (kept.metadata, kept.sent_using, kept.send_status) == ({"a": "1"}, "loud", message.send_status)

    def test_ids_of_their_own(self):
        message = SMSMessage([NUMBER, OTHER], "Hi")

        locmem.SMSBackend().send_messages([message])

        first, second = (status.message_id for status in message.send_status.recipients.values())
        assert first.startswith("locmem-") and second.startswith("locmem-") and first != second

    def test_its_defaults_and_the_options_it_uses(self):
        assert locmem.SMSBackend().alias == "default"
        assert locmem.SMSBackend(status_callback="https://acme/cb").status_callback == "https://acme/cb"

    def test_takes_every_option_a_real_sender_does(self):
        sender = locmem.SMSBackend(account_sid="AC", auth_token="t", from_="Acme", fail_silently=True)

        assert (sender.from_, sender.fail_silently) == ("Acme", True)
        assert (locmem.SMSBackend.takes_any_option, locmem.SMSBackend.sends_nowhere) == (True, False)

    def test_a_scripted_status(self):
        respond(NUMBER, Status.SCHEDULED)

        status = send_sms([NUMBER, OTHER], "Hi")

        assert status.recipients[NUMBER].status is Status.SCHEDULED
        assert status.recipients[OTHER].status is Status.QUEUED

    @pytest.mark.parametrize("refusal", [Status.INVALID, Status.REJECTED])
    def test_a_scripted_refusal_has_no_id(self, refusal):
        respond(NUMBER, refusal)

        with pytest.raises(SMSRecipientsRefused) as refused:
            send_sms(NUMBER, "Hi")

        assert refused.value.status.recipients[NUMBER] == RecipientStatus(refusal)
        assert sms.outbox == []

    def test_a_scripted_recipient_status(self):
        answer = RecipientStatus(Status.SENT, message_id="SM1", price="-0.0079")
        respond(NUMBER, answer)

        assert send_sms(NUMBER, "Hi").recipients[NUMBER] is answer

    def test_a_scripted_exception(self):
        respond(NUMBER, SMSRateLimited("Slow down", retry_after=1.5))

        with pytest.raises(SMSRateLimited, match="Slow down") as raised:
            send_sms(NUMBER, "Hi")

        assert (raised.value.retry_after, raised.value.retryable) == (1.5, True)

        assert sms.outbox == []

    def test_a_cancelled_message_isnt_kept(self):
        def cancel(**kwargs):
            raise SMSCancelSend

        signals.pre_send.connect(cancel)
        try:
            send_sms(NUMBER, "Hi")
        finally:
            signals.pre_send.disconnect(cancel)

        assert sms.outbox == []

    def test_empty_forgets_the_outbox_and_the_scripts(self):
        respond(NUMBER, Status.INVALID)
        send_sms(OTHER, "Hi")

        locmem.empty()

        assert sms.outbox == []
        assert locmem.responses == {}

    def test_makes_the_outbox_where_theres_none(self, monkeypatch):
        monkeypatch.delattr(sms, "outbox")

        locmem.SMSBackend()

        assert sms.outbox == []

    def test_keeps_the_outbox_there_is(self):
        send_sms(NUMBER, "Hi")

        locmem.SMSBackend()

        assert len(sms.outbox) == 1

    def test_every_test_starts_with_an_empty_outbox_part_one(self):
        send_sms(NUMBER, "Hi")
        respond(OTHER, Status.INVALID)

        assert len(sms.outbox) == 1

    def test_every_test_starts_with_an_empty_outbox_part_two(self):
        assert sms.outbox == []
        assert locmem.responses == {}
