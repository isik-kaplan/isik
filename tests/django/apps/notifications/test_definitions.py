"""What each definition says, exactly - fields, declarations, exemptions, serializers, the command's options -
built again at test time, since what runs at import can't be checked any other way."""

import uuid

import pgtrigger
import pytest
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.signals import post_migrate
from django.test.utils import isolate_apps

from isik.django.apps.idempotency.drf import NoIdempotencyKey, NoReplay
from isik.django.apps.notifications import conf, drf, kinds
from isik.django.apps.notifications.audience import bases, swapping
from isik.django.apps.notifications.audience import models as audience_models
from isik.django.apps.notifications.audience.helpers import _kind_name
from isik.django.apps.notifications.audiences import Field, Subscribers
from isik.django.apps.notifications.bases import _delivery_key
from isik.django.apps.notifications.kinds import Kind, SensitiveSnapshotField, declare_kind, notifies
from isik.django.apps.notifications.management.commands.notices import Command
from isik.django.apps.notifications.models import NoticeKindPolicy, Policy, Scope, Source
from isik.django.apps.notifications.partitions import Column
from isik.django.apps.notifications.sync import mirror_kinds
from isik.django.apps.notifications.triggers import NoticeTrigger
from isik.django.drf.viewsets.preconditions import NoPrecondition


def described(field):
    """A field's deconstructed path, args and kwargs - with its lazy help_text read as text."""
    _name, path, args, kwargs = field.deconstruct()
    if "help_text" in kwargs:
        kwargs["help_text"] = str(kwargs["help_text"])
    return path, tuple(args), kwargs


class TestTheFieldsBuiltForEachModel:
    def test_a_deliverys_key(self):
        assert described(_delivery_key("webhook")) == (
            "django.db.models.OneToOneField",
            (),
            {
                "to": "isik_notifications.delivery",
                "on_delete": models.CASCADE,
                "related_name": "webhook",
                "help_text": "The delivery this is the target of.",
                "db_comment": "The delivery this is the target of.",
            },
        )

    def test_a_kind(self):
        assert described(audience_models._kind()) == (
            "django.db.models.ForeignKey",
            (),
            {
                "to": "isik_notifications.noticekindpolicy",
                "on_delete": models.CASCADE,
                "related_name": "+",
                "help_text": "The kind it's for.",
                "db_comment": "The kind it's for.",
            },
        )

    def test_a_user(self):
        path, args, kwargs = described(audience_models._user())

        assert (path, args) == ("django.db.models.ForeignKey", ())
        assert kwargs == {
            "to": settings.AUTH_USER_MODEL.lower(),
            "on_delete": models.CASCADE,
            "related_name": "+",
            "help_text": "The user it's for.",
            "db_comment": "The user it's for.",
        }

    def test_a_variant(self):
        assert described(bases._variant()) == (
            "django.db.models.CharField",
            (),
            {
                "max_length": 100,
                "null": True,
                "blank": True,
                "help_text": "The kind's one variant this is for, or none for every one.",
                "db_comment": "The kind's declared variant this is for, or null for every variant.",
            },
        )


class TestDeclaring:
    @isolate_apps("tests.testapp")
    def test_notifies_keeps_every_argument(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])
        # Named afresh each run: pgtrigger's registry outlives isolate_apps, and a test may run twice.
        name = f"Kept{uuid.uuid4().hex[:8]}"
        audience, condition, partition = Field("owner"), pgtrigger.Q(new__state="x"), Column("schema")
        declare = notifies(
            name.lower(),
            audience=audience,
            policy=Policy.MANDATORY,
            on=pgtrigger.Update,
            condition=condition,
            variants={"v": None},
            snapshot=["state"],
            previous=True,
            scope=Scope.CROSS_PARTITION,
            channels=["inbox", "email"],
            partition=partition,
            sensitive={"state": SensitiveSnapshotField(reason="a state is a word, never a secret, whatever it says")},
        )
        model = declare(
            type(name, (models.Model,), {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})})
        )

        [kind] = kinds.declared
        assert kind == Kind(
            name=name.lower(),
            audience=audience,
            policy=Policy.MANDATORY,
            scope=Scope.CROSS_PARTITION,
            source=Source.TRIGGER,
            variants={"v": None},
            channels=("inbox", "email"),
            model=model,
            on=pgtrigger.Update,
            condition=condition,
            snapshot=("state",),
            previous=True,
            partition=partition,
            sensitive={"state": SensitiveSnapshotField(reason="a state is a word, never a secret, whatever it says")},
        )
        assert kind.declared_in == model._meta.label

    @isolate_apps("tests.testapp")
    def test_what_a_kind_says_declared_where_it_notifies(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])
        monkeypatch.setattr(kinds, "vocabulary", [])
        name = f"Said{uuid.uuid4().hex[:8]}"
        texts = {"label": "Said", "description": "What it says.", "message": "It said.", "title": "Saying"}

        notifies(name.lower(), audience=Subscribers(), policy=Policy.DEFAULT_ON, on=pgtrigger.Delete, **texts)(
            type(name, (models.Model,), {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})})
        )

        assert kinds.text_of(name.lower()) == kinds.KindText(**texts)

    def test_what_a_kind_says_declared_in_the_vocabulary(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])
        monkeypatch.setattr(kinds, "vocabulary", [])
        texts = {"label": "Said", "description": "What it says.", "message": "It said.", "title": "Saying"}

        made = kinds.declare_kind("said", variants=("loud",), **texts)

        assert (made, made.variants, made.text) == ("said", ("loud",), kinds.KindText(**texts))
        assert kinds.text_of("said") == kinds.KindText(**texts)

    @isolate_apps("tests.testapp")
    def test_notifies_defaults(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])
        name = f"Plain{uuid.uuid4().hex[:8]}"
        audience = Subscribers()
        model = notifies(name.lower(), audience=audience, policy=Policy.DEFAULT_ON, on=pgtrigger.Delete)(
            type(name, (models.Model,), {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})})
        )

        [kind] = kinds.declared
        assert (kind.scope, kind.source, kind.variants, kind.channels, kind.condition, kind.previous) == (
            Scope.SINGLE_PARTITION,
            Source.TRIGGER,
            {},
            None,
            None,
            False,
        )
        assert (kind.partition, kind.snapshot, kind.model, kind.audience, kind.policy) == (
            None,
            (),
            model,
            audience,
            Policy.DEFAULT_ON,
        )
        assert (str(kind.on), kind.sensitive) == (str(pgtrigger.Delete), {})

    def test_notifies_has_no_default_for_what_it_watches(self):
        with pytest.raises(TypeError, match="on"):
            notifies("k", audience=Subscribers(), policy=Policy.MANDATORY)

    def test_declare_kind_keeps_every_argument(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])
        monkeypatch.setattr(kinds, "vocabulary", [])
        audience = Subscribers()

        name = declare_kind(
            "k",
            audience=audience,
            policy=Policy.DEFAULT_OFF,
            variants=["a"],
            scope=Scope.CROSS_PARTITION,
            channels=["inbox"],
        )

        assert (name, name.variants) == ("k", ("a",))
        [kind] = kinds.declared
        assert kind == Kind(
            name="k",
            audience=audience,
            policy=Policy.DEFAULT_OFF,
            scope=Scope.CROSS_PARTITION,
            source=Source.APPLICATION,
            variants=("a",),
            channels=("inbox",),
        )
        declare_kind("plain", audience=Subscribers, policy=Policy.MANDATORY)
        assert kinds.get_kind("plain").scope == Scope.SINGLE_PARTITION
        assert isinstance(kinds.get_kind("plain").audience, Subscribers)

    def test_a_notice_trigger_takes_the_kinds_name_operation_and_condition(self):
        kind = kinds.get_kind("parcel_shipped")

        trigger = NoticeTrigger(kind=kind)

        assert (trigger.name, trigger.operation, trigger.condition) == (
            "notify_parcel_shipped",
            kind.on,
            kind.condition,
        )


def test_the_ledger_app_mirrors_after_its_own_migrate_once(monkeypatch):
    connected = []
    monkeypatch.setattr(post_migrate, "connect", lambda *args, **kwargs: connected.append((args, kwargs)))
    config = apps.get_app_config("isik_notifications")

    config.ready()

    assert connected == [((mirror_kinds,), {"sender": config, "dispatch_uid": "isik_notifications_mirror_kinds"})]


class TestTheExemptionsTheViewsMake:
    def test_each_with_its_reason(self):
        made = {name: (type(each), each.reason) for name, each in drf._own_exemptions().items()}

        assert made == {
            "read": (NoPrecondition, "marks the caller's own items read, which an item without a version can't name"),
            "read_key": (NoIdempotencyKey, "marks items read, and a retry finds them read already"),
            "preference": (
                NoIdempotencyKey,
                "sets the caller's preference to the state it names, so a retry changes nothing",
            ),
            "forget": (NoPrecondition, "forgets the caller's own preference, which has no version to name"),
            "forget_key": (NoIdempotencyKey, "forgets a preference, and a retry finds it forgotten"),
            "secret": (NoReplay, "answers with a new secret, shown once and never stored to be shown again"),
            "register": (
                NoIdempotencyKey,
                "registers the token as the caller's device, and a retry finds it registered already",
            ),
            "forget_device": (NoPrecondition, "forgets the caller's own device - a sign-out, not an edit to race"),
            "forget_device_key": (NoIdempotencyKey, "forgets a device, and a retry finds it forgotten"),
            "retry": (
                NoPrecondition,
                "retries a delivery, which only an abandoned one may be - its state is the guard",
            ),
        }


class TestTheSerializers:
    def test_an_inbox_item_is_read_only_its_kind_by_key(self):
        fields = drf.inbox_item_serializer(swapping.get_inbox_item_model())().fields

        assert all(field.read_only for field in fields.values())
        assert fields["kind"].source == "kind.name"

    def test_an_endpoints_counters_are_read_only(self):
        fields = drf.endpoint_serializer(swapping.get_endpoint_model())().fields

        assert {name for name, field in fields.items() if field.read_only} == {
            "id",
            "consecutive_failures",
            "parked_at",
            "secret",
            "updated_at",
        }
        assert fields["kinds"].required is False

    def test_a_devices_state_is_read_only(self):
        fields = drf.device_serializer(swapping.get_device_model())().fields

        assert {name for name, field in fields.items() if field.read_only} == {"id", "is_active", "last_seen_at"}


class TestDeclaringAPush:
    REASON = "the status opens the right tab in the app, and says nothing private"

    def test_what_it_carries(self):
        content = kinds.PushContent()

        assert (content.private, content.data, content.collapse, content.ttl, content.badge) == (
            False,
            {},
            None,
            None,
            False,
        )

    @isolate_apps("tests.testapp")
    def test_notifies_keeps_it(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])
        name = f"Pushed{uuid.uuid4().hex[:8]}"
        content = kinds.PushContent(private=True, data={"state": kinds.PushDataField(reason=self.REASON)})

        notifies(name.lower(), audience=Subscribers(), policy=Policy.DEFAULT_ON, on=pgtrigger.Delete, push=content)(
            type(name, (models.Model,), {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})})
        )

        assert kinds.declared[0].push == content

    def test_notifies_names_the_kind_it_refuses(self):
        with pytest.raises(ImproperlyConfigured, match="^loud's push collapses by 'id'"):
            notifies(
                "loud",
                audience=Subscribers(),
                policy=Policy.DEFAULT_ON,
                on=pgtrigger.Delete,
                push=kinds.PushContent(collapse="id"),
            )

    def test_declare_kind_keeps_it(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])
        monkeypatch.setattr(kinds, "vocabulary", [])
        content = kinds.PushContent(collapse="subject", badge=True)

        declare_kind("k", audience=Subscribers(), policy=Policy.MANDATORY, push=content)
        declare_kind("plain", audience=Subscribers(), policy=Policy.MANDATORY)

        assert (kinds.get_kind("k").push, kinds.get_kind("plain").push) == (content, kinds.PushContent())

    def test_a_field_it_carries_says_why(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])

        with pytest.raises(ImproperlyConfigured, match=r"^k\.push\.data takes"):
            declare_kind("k", audience=Subscribers(), policy=Policy.MANDATORY, push=kinds.PushContent(data=["x"]))

    def test_it_collapses_by_kind_or_subject_alone(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])

        with pytest.raises(
            ImproperlyConfigured, match=r"^k's push collapses by 'id' - it takes kind, subject or None\.$"
        ):
            declare_kind("k", audience=Subscribers(), policy=Policy.MANDATORY, push=kinds.PushContent(collapse="id"))
        assert kinds.declared == []

    def test_a_recorded_kind_has_none(self, monkeypatch):
        with pytest.raises(ImproperlyConfigured, match="^r is recorded"):
            declare_kind("r", source=Source.RECORDED, push=kinds.PushContent())

    def test_one_with_no_behavior_here_takes_it_on_notifies(self, monkeypatch):
        monkeypatch.setattr(kinds, "vocabulary", [])

        with pytest.raises(
            ImproperlyConfigured,
            match="^v's push= goes on the notifies\\(\\) writing it, beside its other behavior\\.$",
        ):
            declare_kind("v", push=kinds.PushContent())
        assert kinds.vocabulary == []


class TestTheCommandsOptions:
    def test_each_option(self):
        parser = Command().create_parser("manage.py", "notices")
        options = {action.dest: action for action in parser._actions}

        assert (options["format"].choices, options["format"].default) == (["text", "json"], "text")
        assert (options["retire"].metavar, options["retire"].help) == (
            "KIND",
            "Retire KIND: its trigger writes nothing more, and its row and notices stay.",
        )


class TestTheSwappableSettings:
    def test_the_endpoint_models_name_is_read_from_its_setting(self, settings):
        settings.ISIK_NOTIFICATIONS_ENDPOINT_MODEL = "testapp.Parcel"

        assert swapping.endpoint_model_name() == "testapp.Parcel"


def test_an_unset_isik_notifications_reads_as_empty(settings):
    del settings.ISIK_NOTIFICATIONS

    assert conf.configured() == {}


@pytest.mark.django_db
def test_a_kind_is_named_by_a_kind_a_policy_row_or_a_string():
    assert _kind_name(kinds.get_kind("parcel_shipped")) == "parcel_shipped"
    assert _kind_name(NoticeKindPolicy.objects.get(name="parcel_shipped")) == "parcel_shipped"
    assert _kind_name("parcel_shipped") == "parcel_shipped"


class TestAnAudienceByItsClass:
    @isolate_apps("tests.testapp")
    def test_one_taking_no_arguments_is_made_for_you(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])
        name = f"Classed{uuid.uuid4().hex[:8]}"

        notifies(name.lower(), audience=Subscribers, policy=Policy.MANDATORY, on=pgtrigger.Insert)(
            type(name, (models.Model,), {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})})
        )

        [kind] = kinds.declared
        assert isinstance(kind.audience, Subscribers)

    def test_one_needing_arguments_says_so_at_once_and_declares_nothing(self):
        vocabulary, declared = list(kinds.vocabulary), list(kinds.declared)

        with pytest.raises(TypeError):
            declare_kind("k", audience=Field, policy=Policy.MANDATORY)

        assert (kinds.vocabulary, kinds.declared) == (vocabulary, declared)

    @isolate_apps("tests.testapp")
    def test_notifies_takes_a_declared_name(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])
        monkeypatch.setattr(kinds, "vocabulary", [])
        named = declare_kind(f"named{uuid.uuid4().hex[:8]}", variants=("a",))
        model_name = f"Named{uuid.uuid4().hex[:8]}"

        notifies(named, audience=Subscribers, policy=Policy.MANDATORY, on=pgtrigger.Insert, variants={"a": None})(
            type(
                model_name,
                (models.Model,),
                {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})},
            )
        )

        [kind] = kinds.declared
        assert type(kind.name) is str
        assert kind.name == named
