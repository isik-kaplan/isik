"""
The deployment's say on a channel - CHANNEL_DEFAULTS, which a kind leaving the channel out is sent under and one
naming it overrides, and CHANNEL_FORCED, the only policy a kind may name there: each held to what a kind's channels
are, mirrored with the kind's own, and shown by `manage.py notices` before the migrate that moves it.
"""

import dataclasses
import json
from io import StringIO

import pytest
from django.core.management import call_command

from isik.django.apps.notifications import conf, kinds
from isik.django.apps.notifications.audience.swapping import get_endpoint_kind_model, get_endpoint_model
from isik.django.apps.notifications.channels import Channel, ChannelName
from isik.django.apps.notifications.checks import kind_problems, setting_problems
from isik.django.apps.notifications.models import NoticeKindPolicy, Policy
from isik.django.apps.notifications.signals import endpoint_kinds_withdrawn
from isik.django.apps.notifications.sync import mirror_kinds
from isik.django.conf import isik_areas
from tests.django.apps.notifications import kind_named


pytestmark = pytest.mark.django_db

DEFAULTS = "ISIK['DJANGO']['NOTIFICATIONS']['CHANNEL_DEFAULTS']"
FORCED = "ISIK['DJANGO']['NOTIFICATIONS']['CHANNEL_FORCED']"


@pytest.fixture
def deployment(settings):
    """Sets CHANNEL_DEFAULTS and CHANNEL_FORCED, beside the test project's own settings."""

    def given(defaults=None, forced=None, **others):
        isik_areas(settings).NOTIFICATIONS = {
            **isik_areas(settings).NOTIFICATIONS,
            **({} if defaults is None else {"CHANNEL_DEFAULTS": defaults}),
            **({} if forced is None else {"CHANNEL_FORCED": forced}),
            **others,
        }

    return given


def shipped(**changes):
    return dataclasses.replace(kinds.get_kind("parcel_shipped"), **changes)


def inboxed(**changes):
    """parcel_shipped, naming the inbox alone - default-on there."""
    return shipped(channels={Channel.INBOX: Policy.DEFAULT_ON}, **changes)


class TestTheSettings:
    def test_unset_say_nothing(self):
        assert (conf.deployment_policies(), setting_problems()) == ({}, [])

    def test_both_valid(self, deployment):
        deployment(defaults={Channel.EMAIL: Policy.DEFAULT_OFF}, forced={Channel.WEBHOOK: Policy.NEVER})

        assert setting_problems() == []
        assert conf.deployment_policies() == {Channel.EMAIL: Policy.DEFAULT_OFF, Channel.WEBHOOK: Policy.NEVER}

    def test_in_the_order_channels_turns_them_on(self, deployment):
        deployment(defaults={Channel.WEBHOOK: Policy.NEVER, Channel.INBOX: Policy.MANDATORY})

        assert list(conf.deployment_policies()) == [Channel.INBOX, Channel.WEBHOOK]

    @pytest.mark.parametrize("key, setting", [("defaults", DEFAULTS), ("forced", FORCED)])
    def test_other_than_a_mapping(self, deployment, key, setting):
        deployment(**{key: [Channel.WEBHOOK]})

        assert setting_problems() == [f"{setting} is {{channel: Policy}}, not ['webhook']."]
        assert conf.deployment_policies() == {}

    @pytest.mark.parametrize("key, setting", [("defaults", DEFAULTS), ("forced", FORCED)])
    def test_a_channel_by_plain_text(self, deployment, key, setting):
        deployment(**{key: {"webhook": Policy.NEVER, Channel.EMAIL: Policy.NEVER}})

        assert setting_problems() == [
            f"{setting} names webhook by plain text - name each as Channel.EMAIL does, or by the ChannelName "
            "register_channel() returned."
        ]

    @pytest.mark.parametrize("key, setting", [("defaults", DEFAULTS), ("forced", FORCED)])
    def test_a_channel_channels_doesnt_turn_on(self, deployment, key, setting):
        deployment(**{key: {Channel.PUSH: Policy.NEVER, ChannelName("fax"): Policy.NEVER}})

        assert setting_problems() == [
            f"{setting} names push, fax, which ISIK['DJANGO']['NOTIFICATIONS']['CHANNELS'] doesn't turn on."
        ]
        assert conf.deployment_policies() == {}

    @pytest.mark.parametrize("key, setting", [("defaults", DEFAULTS), ("forced", FORCED)])
    def test_something_other_than_a_policy(self, deployment, key, setting):
        deployment(**{key: {Channel.INBOX: "sometimes", Channel.EMAIL: Policy.NEVER}})

        assert setting_problems() == [f"{setting} gives inbox something other than a Policy."]

    @pytest.mark.parametrize("key, setting", [("defaults", DEFAULTS), ("forced", FORCED)])
    @pytest.mark.parametrize("policy", [Policy.DEFAULT_ON, Policy.DEFAULT_OFF])
    def test_a_choice_where_no_person_chooses(self, deployment, key, setting, policy):
        deployment(**{key: {Channel.WEBHOOK: policy, Channel.INBOX: Policy.DEFAULT_OFF}})

        assert setting_problems() == [
            f"{setting} leaves webhook to each person's choice, and no person chooses there - "
            "Policy.MANDATORY or Policy.NEVER."
        ]

    def test_a_choice_on_a_channel_not_turned_on_is_named_once(self, deployment):
        deployment(defaults={ChannelName("fax"): Policy.DEFAULT_ON})

        assert setting_problems() == [
            f"{DEFAULTS} names fax, which ISIK['DJANGO']['NOTIFICATIONS']['CHANNELS'] doesn't turn on."
        ]

    def test_a_channel_in_both(self, deployment):
        deployment(
            defaults={Channel.WEBHOOK: Policy.NEVER, Channel.INBOX: Policy.MANDATORY, Channel.EMAIL: Policy.NEVER},
            forced={Channel.EMAIL: Policy.NEVER, Channel.WEBHOOK: Policy.NEVER},
        )

        assert setting_problems() == [
            "ISIK['DJANGO']['NOTIFICATIONS'] both defaults and forces webhook, email - a forced policy is the only "
            "one there, so its default is never read. Name each channel in one of them."
        ]
        assert conf.deployment_policies()[Channel.INBOX] == Policy.MANDATORY


class TestAKind:
    def test_leaving_out_a_defaulted_channel_is_sent_there_under_it(self, deployment, declaring):
        deployment(defaults={Channel.EMAIL: Policy.NEVER, Channel.WEBHOOK: Policy.MANDATORY})
        kind = inboxed()
        declaring(kind)

        assert kind_problems() == []
        assert kind.policies == {
            Channel.INBOX: Policy.DEFAULT_ON,
            Channel.EMAIL: Policy.NEVER,
            Channel.WEBHOOK: Policy.MANDATORY,
        }
        assert (kind.enabled_channels, kind.chosen_channels) == ((Channel.INBOX, Channel.WEBHOOK), (Channel.INBOX,))
        assert [kind.policy_set_by(channel) for channel in kind.policies] == ["declared", "default", "default"]

    def test_naming_a_defaulted_channel_keeps_its_own(self, deployment, declaring):
        deployment(defaults={Channel.WEBHOOK: Policy.NEVER})
        kind = shipped()
        declaring(kind)

        assert kind_problems() == []
        assert (kind.policy_on(Channel.WEBHOOK), kind.policy_set_by(Channel.WEBHOOK)) == (Policy.MANDATORY, "declared")

    def test_leaving_out_a_forced_channel_is_sent_there_under_it(self, deployment, declaring):
        deployment(defaults={Channel.EMAIL: Policy.DEFAULT_OFF}, forced={Channel.WEBHOOK: Policy.NEVER})
        kind = inboxed()
        declaring(kind)

        assert kind_problems() == []
        assert (kind.policy_on(Channel.WEBHOOK), kind.policy_set_by(Channel.WEBHOOK)) == (Policy.NEVER, "forced")
        assert kind.policy_set_by(Channel.EMAIL) == "default"

    def test_naming_a_forced_channel_with_its_policy(self, deployment, declaring):
        deployment(forced={Channel.WEBHOOK: Policy.MANDATORY})
        declaring(shipped())

        assert kind_problems() == []

    def test_naming_a_forced_channel_with_another_policy_is_refused(self, deployment, declaring):
        deployment(forced={Channel.WEBHOOK: Policy.NEVER, Channel.INBOX: Policy.MANDATORY})
        declaring(shipped(), kinds.get_kind("parcel_routed"))

        assert kind_problems() == [
            "parcel_shipped is sent on inbox under default_on, and ISIK['DJANGO']['NOTIFICATIONS']['CHANNEL_FORCED'] "
            "forces mandatory there - change the kind, or leave inbox out of it.",
            "parcel_shipped is sent on webhook under mandatory, and ISIK['DJANGO']['NOTIFICATIONS']['CHANNEL_FORCED'] "
            "forces never there - change the kind, or leave webhook out of it.",
        ]

    def test_a_policy_that_isnt_one_is_named_once(self, deployment, declaring):
        deployment(forced={Channel.WEBHOOK: Policy.NEVER})
        declaring(shipped(channels={**shipped().channels, Channel.WEBHOOK: "sometimes"}))

        assert kind_problems() == ["parcel_shipped gives webhook something other than a Policy."]

    def test_leaving_out_a_channel_neither_names_is_refused(self, deployment, declaring):
        deployment(defaults={Channel.EMAIL: Policy.NEVER})
        declaring(inboxed())

        assert kind_problems() == [
            "parcel_shipped says nothing of webhook - its channels name every channel "
            "ISIK['DJANGO']['NOTIFICATIONS']['CHANNELS'] turns on that CHANNEL_DEFAULTS and CHANNEL_FORCED don't, "
            "Policy.NEVER where it never goes out."
        ]

    def test_a_channel_not_turned_on_is_set_by_no_one(self, deployment):
        deployment(defaults={Channel.EMAIL: Policy.NEVER})

        assert inboxed().policy_set_by(Channel.PUSH) is None

    def test_a_recorded_kind_is_outside_both(self, deployment, declaring):
        deployment(defaults={Channel.INBOX: Policy.MANDATORY}, forced={Channel.EMAIL: Policy.NEVER})
        recorded = kinds.get_kind("parcel_receipt")
        declaring(recorded)

        assert kind_problems() == []
        assert recorded.policies == {Channel.EMAIL: Policy.MANDATORY}
        assert recorded.policy_set_by(Channel.INBOX) is None


class TestTheMirror:
    def test_writes_the_deployments_policies_with_the_kinds_own(self, deployment, declaring):
        deployment(defaults={Channel.EMAIL: Policy.NEVER, Channel.WEBHOOK: Policy.MANDATORY})
        declaring(inboxed())

        mirror_kinds()

        assert NoticeKindPolicy.objects.get(name="parcel_shipped").channels == {
            "inbox": "default_on",
            "email": "never",
            "webhook": "mandatory",
        }

    def test_a_force_moving_a_kind_off_webhook_withdraws_its_endpoints(
        self, deployment, declaring, django_capture_on_commit_callbacks
    ):
        endpoint = get_endpoint_model().objects.create(url="https://hooks.example/a", secret="s")
        get_endpoint_kind_model().objects.create(endpoint=endpoint, kind=kind_named("parcel_shipped"))
        heard = []
        receiver = lambda sender, kind, endpoint_ids, **kwargs: heard.append((kind, endpoint_ids))  # noqa: E731
        endpoint_kinds_withdrawn.connect(receiver)
        deployment(forced={Channel.WEBHOOK: Policy.NEVER})
        declaring(shipped(channels={Channel.INBOX: Policy.DEFAULT_ON, Channel.EMAIL: Policy.DEFAULT_ON}))

        try:
            with django_capture_on_commit_callbacks(execute=True):
                mirror_kinds()
        finally:
            endpoint_kinds_withdrawn.disconnect(receiver)

        assert heard == [("parcel_shipped", [str(endpoint.pk)])]


class TestNotices:
    def listing(self, *args):
        out = StringIO()
        call_command("notices", *args, stdout=out)
        return out.getvalue()

    def test_shows_which_channels_the_deployment_set(self, deployment, declaring):
        deployment(defaults={Channel.EMAIL: Policy.NEVER}, forced={Channel.WEBHOOK: Policy.NEVER})
        declaring(inboxed())

        (kind,) = json.loads(self.listing("--format", "json"))["kinds"]

        assert kind["set_by"] == {"email": "default", "webhook": "forced"}
        assert "  set_by: email=default, webhook=forced\n" in self.listing()

    def test_shows_no_set_by_for_a_kind_naming_every_channel(self):
        assert "set_by" not in self.listing()

    def test_shows_what_the_next_migrate_changes_and_what_it_withdraws(self, deployment, declaring):
        endpoint = get_endpoint_model().objects.create(url="https://hooks.example/a", secret="s")
        other = get_endpoint_model().objects.create(url="https://hooks.example/b", secret="s")
        for each in (endpoint, other):
            get_endpoint_kind_model().objects.create(endpoint=each, kind=kind_named("parcel_shipped"))
        deployment(forced={Channel.WEBHOOK: Policy.NEVER, Channel.INBOX: Policy.MANDATORY})
        declaring(
            shipped(channels={Channel.EMAIL: Policy.DEFAULT_ON}),
            kinds.get_kind("parcel_routed"),
            kinds.get_kind("parcel_receipt"),
        )

        pending = json.loads(self.listing("--format", "json"))["pending"]

        assert pending == [
            {"kind": "parcel_shipped", "channel": "inbox", "now": "default_on", "after": "mandatory", "withdrawn": 0},
            {"kind": "parcel_shipped", "channel": "webhook", "now": "mandatory", "after": "never", "withdrawn": 2},
        ]
        listed = self.listing()
        assert "parcel_shipped on inbox: default_on now, mandatory after the next migrate\n" in listed
        assert (
            "parcel_shipped on webhook: mandatory now, never after the next migrate - 2 endpoints' subscriptions to "
            "it go inert\n"
        ) in listed

    def test_a_kind_moving_off_webhook_with_no_endpoints_withdraws_none(self, deployment, declaring):
        deployment(forced={Channel.WEBHOOK: Policy.NEVER})
        declaring(shipped(channels={Channel.INBOX: Policy.DEFAULT_ON, Channel.EMAIL: Policy.DEFAULT_ON}))

        assert json.loads(self.listing("--format", "json"))["pending"] == [
            {"kind": "parcel_shipped", "channel": "webhook", "now": "mandatory", "after": "never", "withdrawn": 0}
        ]

    def test_a_channel_the_row_lacks_reads_as_never(self, declaring):
        NoticeKindPolicy.objects.filter(name="parcel_shipped").update(
            channels={"inbox": "default_on", "email": "default_on"}
        )

        assert json.loads(self.listing("--format", "json"))["pending"] == [
            {"kind": "parcel_shipped", "channel": "webhook", "now": "never", "after": "mandatory", "withdrawn": 0}
        ]

    def test_nothing_pending_once_mirrored(self):
        assert json.loads(self.listing("--format", "json"))["pending"] == []
