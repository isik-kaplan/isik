"""Declaring kinds, raising application kinds, audiences, the subscription helpers, settings and the mirror."""

import uuid
from contextlib import contextmanager
from datetime import timedelta

import pghistory
import pgtrigger
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test.utils import isolate_apps
from django.utils import timezone

from isik.django.apps.notifications import conf, kinds
from isik.django.apps.notifications.audience import (
    get_endpoint_kind_model,
    get_endpoint_model,
    get_inbox_item_model,
    get_subscription_model,
    swapping,
)
from isik.django.apps.notifications.audience.bases import AbstractSubscription
from isik.django.apps.notifications.audience.helpers import mark_read, subscribe, unread, unsubscribe
from isik.django.apps.notifications.audiences import LIVE, Audience, Field, Subscribers
from isik.django.apps.notifications.kinds import Kind, declare_kind, get_kind, matching_variant, notifies, raise_notice
from isik.django.apps.notifications.models import Notice, NoticeKindPolicy, Policy, Scope, Source
from isik.django.apps.notifications.partitions import PartitionGone, in_partition
from isik.django.apps.notifications.sync import mirror_kinds
from tests.testapp.models import PARCEL_REMINDER, Parcel


class TestTheRegistry:
    def test_the_test_projects_kinds(self):
        assert {"parcel_shipped", "parcel_flagged", "parcel_reminder"} <= set(kinds.kinds())

    def test_a_trigger_kind(self):
        kind = get_kind("parcel_flagged")

        assert (kind.model, kind.source, kind.policy, kind.scope) == (
            Parcel,
            Source.TRIGGER,
            Policy.DEFAULT_OFF,
            Scope.SINGLE_PARTITION,
        )
        assert list(kind.variants) == ["lost", "damaged"]
        assert kind.trigger_name == "notify_parcel_flagged"
        assert kind.declared_in == "testapp.Parcel"
        assert kind.enabled_channels == ("inbox", "email", "webhook")

    def test_an_application_kind(self):
        kind = get_kind(PARCEL_REMINDER)

        assert (PARCEL_REMINDER, PARCEL_REMINDER.variants) == ("parcel_reminder", ("daily", "weekly"))
        assert isinstance(PARCEL_REMINDER, str)
        assert (kind.source, kind.variants, kind.model) == (Source.APPLICATION, ("daily", "weekly"), None)
        assert isinstance(kind.audience, Subscribers)

    def test_a_kind_without_channels_takes_the_configured_ones(self, settings):
        settings.ISIK_NOTIFICATIONS = {"CHANNELS": ("inbox",)}

        assert get_kind("parcel_shipped").enabled_channels == ("inbox",)

    def test_an_unknown_name(self):
        assert get_kind("nothing") is None

    def test_the_last_declaration_of_a_name_wins(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])
        monkeypatch.setattr(kinds, "vocabulary", [])
        declare_kind("twice", audience=Subscribers(), policy=Policy.MANDATORY)
        declare_kind("twice", audience=Subscribers(), policy=Policy.DEFAULT_ON, channels=["inbox"])

        first, second = kinds.declared
        assert get_kind("twice") is second
        assert (first.policy, second.channels) == (Policy.MANDATORY, ("inbox",))
        assert kinds.vocabulary == ["twice", "twice"]

    def test_declaring_only_the_vocabulary_writes_no_behavior(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])
        monkeypatch.setattr(kinds, "vocabulary", [])

        name = declare_kind("later", variants=("a",))

        assert (kinds.declared, kinds.vocabulary, name.variants) == ([], ["later"], ("a",))

    @isolate_apps("tests.testapp")
    def test_notifies_registers_its_trigger(self, monkeypatch):
        from django.db import models

        monkeypatch.setattr(kinds, "declared", [])
        # Named afresh each run: pgtrigger's registry outlives isolate_apps, and a test may run twice.
        name = f"Opened{uuid.uuid4().hex[:8]}"
        declare = notifies(
            name.lower(), audience=Subscribers(), policy=Policy.MANDATORY, on=pgtrigger.Insert, channels=["inbox"]
        )
        box = declare(
            type(name, (models.Model,), {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})})
        )

        [kind] = kinds.declared
        assert (str(kind.on), kind.condition, kind.variants, kind.snapshot) == (
            str(pgtrigger.Insert),
            None,
            {},
            (),
        )
        assert [trigger.name for trigger in box._meta.triggers] == [f"notify_{name.lower()}"]


@pytest.mark.django_db
class TestRaisingANotice:
    def test_it_writes_one_in_the_transaction(self, alice):
        notice = raise_notice("parcel_reminder", variant="daily", subject=alice, snapshot={"n": 1}, partition="p")

        assert Notice.objects.get() == notice
        assert (notice.kind_id, notice.variant, notice.subject_label, notice.subject_id) == (
            "parcel_reminder",
            "daily",
            "testapp.EmailUser",
            str(alice.pk),
        )
        assert (notice.snapshot, notice.subject_partition, notice.pgh_context) == ({"n": 1}, "p", None)

    def test_one_about_nothing(self):
        notice = raise_notice(PARCEL_REMINDER)

        assert (notice.variant, notice.subject_label, notice.subject_id, notice.snapshot) == (None, None, None, {})

    def test_it_carries_the_history_context(self):
        with pghistory.context(why="reminding"):
            notice = raise_notice("parcel_reminder")

        assert notice.pgh_context.metadata == {"why": "reminding"}

    @pytest.mark.parametrize("kind", ["parcel_shipped", "nothing"])
    def test_only_an_application_kind(self, kind):
        with pytest.raises(ImproperlyConfigured, match=f"^{kind} is not a declared kind whose source is application.$"):
            raise_notice(kind)

    def test_only_a_declared_variant(self):
        with pytest.raises(ValueError, match="^parcel_reminder declares no variant 'hourly'.$"):
            raise_notice("parcel_reminder", variant="hourly")

    def test_not_a_retired_kind(self):
        NoticeKindPolicy.objects.filter(pk="parcel_reminder").update(retired_at=timezone.now())

        with pytest.raises(ImproperlyConfigured, match="^parcel_reminder is retired.$"):
            raise_notice("parcel_reminder")
        assert not Notice.objects.exists()


@pytest.mark.django_db
def test_matching_a_variant_takes_rows_naming_it_or_none(alice, bob):
    model = get_subscription_model()
    every = model.objects.create(user=alice, kind_id="parcel_flagged")
    lost = model.objects.create(user=bob, kind_id="parcel_flagged", variant="lost")
    model.objects.create(user=bob, kind_id="parcel_flagged", variant="damaged")

    assert set(matching_variant(model.objects.all(), "lost")) == {every, lost}
    assert set(matching_variant(model.objects.all(), None)) == {every}


@pytest.mark.django_db
class TestAudiences:
    def notice(self, parcel):
        Parcel.objects.filter(pk=parcel.pk).update(status="shipped")
        return Notice.objects.get(kind_id="parcel_shipped")

    def test_field_reads_the_snapshot(self, parcel, alice, bob):
        notice = self.notice(parcel)
        Parcel.objects.filter(pk=parcel.pk).update(recipient=alice)

        assert list(Field("recipient").recipients(notice)) == [bob]
        assert set(Field("recipient", "sender").recipients(notice)) == {alice, bob}

    def test_field_at_live_reads_the_row_as_it_is(self, parcel, alice):
        notice = self.notice(parcel)
        Parcel.objects.filter(pk=parcel.pk).update(recipient=alice)

        assert list(Field("recipient", at=LIVE).recipients(notice)) == [alice]

    def test_field_at_live_with_the_row_gone_names_nobody(self, parcel):
        notice = self.notice(parcel)
        Parcel.objects.filter(pk=parcel.pk).delete()

        assert list(Field("recipient", at=LIVE).recipients(notice)) == []

    def test_an_empty_key_names_nobody(self, alice):
        parcel = Parcel.objects.create(sender=alice)

        assert list(Field("recipient").recipients(self.notice(parcel))) == []

    def test_the_fields_it_reads_from_the_snapshot(self):
        assert Field("a", "b").snapshot_fields() == ("a", "b")
        assert Field("a", at=LIVE).snapshot_fields() == ()
        assert Audience().snapshot_fields() == ()

    def test_how_each_reads(self):
        assert str(Field("a", "b")) == "Field('a', 'b')"
        assert str(Field("a", at=LIVE)) == "Field('a', at=LIVE)"
        assert str(Subscribers()) == "Subscribers"

    def test_field_names_something(self):
        with pytest.raises(TypeError, match="^Field\\(\\) names at least one field.$"):
            Field()
        with pytest.raises(ValueError, match="^Field\\(\\)'s at= is SNAPSHOT or LIVE, not 'now'.$"):
            Field("a", at="now")

    def test_the_base(self, parcel):
        notice = self.notice(parcel)

        assert Audience().partitions(notice) == [None]
        assert Audience.spans_partitions is False
        with pytest.raises(NotImplementedError):
            Audience().recipients(notice)

    def test_subscribers_are_the_enabled_ones_covering_the_variant(self, alice, bob):
        subscribe(alice, "parcel_reminder")
        subscribe(bob, "parcel_reminder", "weekly")
        notice = raise_notice("parcel_reminder", variant="daily")

        assert list(Subscribers().recipients(notice)) == [alice]
        subscribe(alice, "parcel_reminder", enabled=False)
        assert list(Subscribers().recipients(notice)) == []


@pytest.mark.django_db
class TestSubscribing:
    def rows(self, user):
        return sorted(
            get_subscription_model().objects.filter(user=user).values_list("kind_id", "variant", "enabled"),
            key=str,
        )

    def test_to_every_variant_replaces_the_ones_named(self, alice):
        subscribe(alice, "parcel_flagged", "lost")
        subscribe(alice, "parcel_flagged", "damaged", enabled=False)

        subscription = subscribe(alice, get_kind("parcel_flagged"))

        assert self.rows(alice) == [("parcel_flagged", None, True)]
        assert subscription.variant is None

    def test_again_updates_it(self, alice):
        subscribe(alice, "parcel_flagged", "lost")
        subscribe(alice, "parcel_flagged", "lost", enabled=False)

        assert self.rows(alice) == [("parcel_flagged", "lost", False)]

    def test_to_a_variant_beside_every_one_is_refused(self, alice):
        subscribe(alice, "parcel_flagged")

        with pytest.raises(
            ValueError,
            match=(
                "^alice@example.com has a subscription to every variant of parcel_flagged - unsubscribe it before "
                "naming one.$"
            ),
        ):
            subscribe(alice, "parcel_flagged", "lost")

    def test_a_kind_by_its_policy_row(self, alice):
        subscribe(alice, NoticeKindPolicy.objects.get(pk="parcel_shipped"), enabled=False)

        assert self.rows(alice) == [("parcel_shipped", None, False)]

    def test_unsubscribing_from_one_variant_or_all(self, alice, bob):
        subscribe(alice, "parcel_flagged", "lost")
        subscribe(alice, "parcel_flagged", "damaged")
        subscribe(bob, "parcel_flagged", "lost")

        assert unsubscribe(alice, "parcel_flagged", "lost") == 1
        assert self.rows(alice) == [("parcel_flagged", "damaged", True)]
        assert unsubscribe(alice, "parcel_flagged") == 1
        assert self.rows(alice) == []
        assert len(self.rows(bob)) == 1


@pytest.mark.django_db
def test_the_inbox_unread_and_marked_read(alice, bob):
    items = get_inbox_item_model().objects
    first = items.create(user=alice, kind_id="parcel_shipped", delivery_id=uuid.uuid4())
    second = items.create(user=alice, kind_id="parcel_shipped", delivery_id=uuid.uuid4())
    items.create(user=bob, kind_id="parcel_shipped", delivery_id=uuid.uuid4())

    assert list(unread(alice)) == [second, first]
    assert mark_read(items.filter(pk=first.pk)) == 1
    assert list(unread(alice)) == [second]
    assert mark_read(items.filter(user=alice)) == 1
    assert mark_read(items.filter(user=alice)) == 0


class TestSwapping:
    def test_the_getters_default_to_isiks_models(self):
        assert [
            model._meta.label
            for model in (
                get_subscription_model(),
                get_inbox_item_model(),
                get_endpoint_model(),
                get_endpoint_kind_model(),
            )
        ] == [
            "isik_notifications_audience.Subscription",
            "isik_notifications_audience.InboxItem",
            "isik_notifications_audience.Endpoint",
            "isik_notifications_audience.EndpointKind",
        ]

    def test_a_setting_names_another(self, settings):
        settings.ISIK_NOTIFICATIONS_SUBSCRIPTION_MODEL = "testapp.Parcel"

        assert get_subscription_model() is Parcel

    def test_defaults_are_given_only_where_unset(self, settings):
        del settings.ISIK_NOTIFICATIONS_ENDPOINT_MODEL
        settings.ISIK_NOTIFICATIONS_INBOX_ITEM_MODEL = "testapp.Parcel"

        swapping.set_defaults()

        assert settings.ISIK_NOTIFICATIONS_ENDPOINT_MODEL == "isik_notifications_audience.Endpoint"
        assert settings.ISIK_NOTIFICATIONS_INBOX_ITEM_MODEL == "testapp.Parcel"
        assert swapping.endpoint_model_name() == "isik_notifications_audience.Endpoint"

    @isolate_apps("tests.testapp")
    def test_a_projects_own_subscription_is_guarded_and_constrained(self):
        from django.conf import settings
        from django.db import models

        # Named afresh each run: pgtrigger's registry outlives isolate_apps, and a test may run twice.
        name = f"Choice{uuid.uuid4().hex[:8]}"
        Choice = type(
            name,
            (AbstractSubscription,),
            {
                "__module__": __name__,
                "user": models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"),
                "kind": models.ForeignKey(
                    "isik_notifications.NoticeKindPolicy", on_delete=models.CASCADE, related_name="+"
                ),
                "Meta": type(
                    "Meta",
                    (),
                    {
                        "app_label": "testapp",
                        "constraints": [
                            models.CheckConstraint(condition=models.Q(enabled__in=[True, False]), name="c")
                        ],
                    },
                ),
            },
        )
        ChoiceProxy = type(
            f"{name}Proxy",
            (Choice,),
            {"__module__": __name__, "Meta": type("Meta", (), {"proxy": True, "app_label": "testapp"})},
        )

        assert [constraint.name for constraint in Choice._meta.constraints] == ["c", f"testapp_{name.lower()}_once"]
        assert Choice._meta.original_attrs["constraints"] == Choice._meta.constraints
        assert [trigger.name for trigger in Choice._meta.triggers] == ["guard_variant"]
        # A proxy shares its model's table, guard and constraint included - it gets none of its own.
        assert (ChoiceProxy._meta.constraints, getattr(ChoiceProxy._meta, "triggers", [])) == ([], [])

    @pytest.mark.django_db
    def test_each_audience_row_reads_as_its_kind(self, alice):
        subscription = subscribe(alice, "parcel_flagged", "lost")
        every = subscribe(alice, "parcel_shipped")
        endpoint = get_endpoint_model().objects.create(url="https://x.example/", secret="s")
        wanted = get_endpoint_kind_model().objects.create(endpoint=endpoint, kind_id="parcel_shipped")
        item = get_inbox_item_model().objects.create(
            user=alice,
            kind_id="parcel_shipped",
            subject_label="testapp.Parcel",
            subject_id="7",
            delivery_id=uuid.uuid4(),
        )

        assert [str(row) for row in (subscription, every, endpoint, wanted, item)] == [
            "parcel_flagged:lost",
            "parcel_shipped:*",
            "https://x.example/",
            "parcel_shipped:*",
            "parcel_shipped:testapp.Parcel:7",
        ]


class TestTheSettings:
    def test_each_has_a_default(self, settings):
        settings.ISIK_NOTIFICATIONS = None

        assert conf.setting("MAX_ATTEMPTS") == 5
        assert conf.setting("REDACTOR") is None
        assert conf.setting("EMAIL_SENDER").__name__ == "send_from_templates"
        assert conf.partitioned() is False

    def test_a_hook_by_its_path(self, settings):
        settings.ISIK_NOTIFICATIONS = {"REDACTOR": "copy.deepcopy"}

        import copy

        assert conf.setting("REDACTOR") is copy.deepcopy

    def test_partitioned_takes_both_hooks(self, settings):
        settings.ISIK_NOTIFICATIONS = {"PARTITIONS": list}
        assert conf.partitioned() is False
        settings.ISIK_NOTIFICATIONS = {"PARTITIONS": list, "IN_PARTITION": "contextlib.nullcontext"}
        assert conf.partitioned() is True

    @pytest.mark.parametrize(
        ("configured", "header"),
        [
            ({}, None),
            ({"PROJECT_NAME": "Acme"}, "X-Acme-Signature"),
            ({"PROJECT_NAME": "Acme Corp"}, "X-Acme-Corp-Signature"),
            ({"PROJECT_NAME": "Acme", "SIGNATURE": {"header": "Webhook-Signature"}}, "Webhook-Signature"),
            ({"SIGNATURE": {"header": ""}}, None),
        ],
    )
    def test_the_signature_header(self, settings, configured, header):
        settings.ISIK_NOTIFICATIONS = configured

        assert conf.signature_header() == header


class TestEnteringAPartition:
    def test_nothing_without_partitions_not_even_asking(self, settings):
        def refuse(*args):
            raise AssertionError("a single-partition project asked a partition hook")

        settings.ISIK_NOTIFICATIONS = {"PARTITION_EXISTS": refuse}

        with in_partition("north") as entered:
            assert entered is None

    @pytest.mark.django_db
    def test_the_projects_hook_once_it_exists(self, settings):
        entered = []

        @contextmanager
        def enter(key):
            entered.append(key)
            yield

        settings.ISIK_NOTIFICATIONS = {"PARTITIONS": lambda: ["north"], "IN_PARTITION": enter}

        with in_partition("north"):
            assert entered == ["north"]

    @pytest.mark.django_db
    @pytest.mark.parametrize("key", ["south", None])
    def test_one_not_listed_is_gone_and_never_entered(self, settings, key):
        entered = []

        @contextmanager
        def enter(key):
            entered.append(key)
            yield

        settings.ISIK_NOTIFICATIONS = {"PARTITIONS": lambda: ["north"], "IN_PARTITION": enter}

        with pytest.raises(PartitionGone, match=f"^The partition {key!r} no longer exists.$") as gone:
            with in_partition(key):
                raise AssertionError("worked in a partition that's gone")

        assert (gone.value.key, entered) == (key, [])

    @pytest.mark.django_db
    def test_the_projects_existence_check_over_the_list(self, settings):
        asked = []
        settings.ISIK_NOTIFICATIONS = {
            "PARTITIONS": lambda: ["north"],
            "IN_PARTITION": "contextlib.nullcontext",
            "PARTITION_EXISTS": lambda key: asked.append(key) or key == "south",
        }

        with in_partition("south"):
            pass
        with pytest.raises(PartitionGone):
            with in_partition("north"):
                pass
        assert asked == ["south", "north"]

    @pytest.mark.django_db
    def test_the_check_and_the_work_share_a_transaction(self, settings):
        from django.db import connection

        depths = []
        settings.ISIK_NOTIFICATIONS = {
            "PARTITIONS": lambda: ["north"],
            "IN_PARTITION": "contextlib.nullcontext",
            "PARTITION_EXISTS": lambda key: depths.append(len(connection.savepoint_ids)) or True,
        }
        outside = len(connection.savepoint_ids)

        with in_partition("north"):
            depths.append(len(connection.savepoint_ids))

        assert depths == [outside + 1, outside + 1]


@pytest.mark.django_db
class TestTheMirror:
    def test_each_declared_kind_has_its_row(self):
        rows = {row.kind: row for row in NoticeKindPolicy.objects.all()}

        assert (rows["parcel_flagged"].policy, rows["parcel_flagged"].variants) == ("default_off", ["lost", "damaged"])
        assert (rows["parcel_reminder"].source, rows["parcel_reminder"].scope) == ("application", "single_partition")

    def test_it_brings_a_row_up_to_date_and_deletes_none(self, monkeypatch):
        NoticeKindPolicy.objects.filter(pk="parcel_shipped").update(policy="mandatory", variants=["x"])
        retired_at = timezone.now() - timedelta(days=1)
        NoticeKindPolicy.objects.create(kind="gone", policy="mandatory", scope="single_partition", source="trigger")
        NoticeKindPolicy.objects.filter(pk="parcel_reminder").update(retired_at=retired_at)

        rows = mirror_kinds()

        assert {row.kind for row in rows} == set(kinds.kinds())
        shipped = NoticeKindPolicy.objects.get(pk="parcel_shipped")
        assert (shipped.policy, shipped.variants) == ("default_on", [])
        assert NoticeKindPolicy.objects.filter(pk="gone").exists()
        assert NoticeKindPolicy.objects.get(pk="parcel_reminder").retired_at == retired_at
        assert str(shipped) == "parcel_shipped"


@pytest.mark.django_db
def test_a_notice_reads_as_its_kind_and_number(parcel):
    Parcel.objects.filter(pk=parcel.pk).update(status="shipped")
    notice = Notice.objects.get()

    assert str(notice) == f"parcel_shipped#{notice.pk}"


def test_the_kind_dataclass_defaults():
    kind = Kind(name="k", audience=Subscribers(), policy=Policy.MANDATORY)

    assert (kind.scope, kind.source, kind.snapshot, kind.previous, kind.partition) == (
        Scope.SINGLE_PARTITION,
        Source.TRIGGER,
        (),
        False,
        None,
    )


@pytest.mark.django_db
def test_the_delivery_reads_as_its_target(parcel):
    from isik.django.apps.notifications.models import Delivery

    Parcel.objects.filter(pk=parcel.pk).update(status="shipped")
    delivery = Delivery.objects.create(notice=Notice.objects.get(), channel="inbox", target_key="user:1")

    assert str(delivery) == "inbox:user:1"
