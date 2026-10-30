"""isik.E014 and isik.W004 - every notifications setting and declaration that can't work."""

import dataclasses

import pgtrigger
import pytest
from django.apps import apps
from django.db import DatabaseError
from django.utils import timezone

from isik.django.apps.common.checks import Check, CheckOption
from isik.django.apps.notifications import checks, conf, kinds
from isik.django.apps.notifications.audiences import Audience, Field, Subscribers
from isik.django.apps.notifications.channels import registered
from isik.django.apps.notifications.checks import (
    kind_problems,
    notification_problems,
    retired_problems,
    setting_problems,
)
from isik.django.apps.notifications.exemptions import SharedEndpoints
from isik.django.apps.notifications.kinds import Kind
from isik.django.apps.notifications.models import Notice, NoticeKindPolicy, Policy, Scope, Source
from isik.django.apps.notifications.partitions import Connection
from tests.testapp.models import WidgetEvent


def test_the_test_project_has_none():
    assert notification_problems() == []


class TestTheSettings:
    def test_unknown_keys(self, settings):
        settings.ISIK_NOTIFICATIONS = {"PROJECT_NAME": "Testapp", "RETRIES": 3, "COLOR": "red"}

        assert setting_problems() == ["ISIK_NOTIFICATIONS has keys it doesn't know: COLOR, RETRIES."]

    @pytest.mark.parametrize(
        ("configured", "problem"),
        [
            ({"RECORD_ATTEMPTS": "some"}, "ISIK_NOTIFICATIONS' RECORD_ATTEMPTS is one of all, failures or none."),
            ({"MAX_ATTEMPTS": 0}, "ISIK_NOTIFICATIONS' MAX_ATTEMPTS is at least 1."),
            ({"BACKOFF": ()}, "ISIK_NOTIFICATIONS' BACKOFF names at least one delay."),
            (
                {"PARTITIONS": list},
                "ISIK_NOTIFICATIONS sets one of PARTITIONS and IN_PARTITION - set both, or neither.",
            ),
            (
                {"IN_PARTITION": "contextlib.nullcontext"},
                "ISIK_NOTIFICATIONS sets one of PARTITIONS and IN_PARTITION - set both, or neither.",
            ),
            (
                {"CHANNELS": ("inbox", "fax", "pigeon")},
                "ISIK_NOTIFICATIONS' CHANNELS names channels nothing registered: fax, pigeon.",
            ),
        ],
    )
    def test_each_invalid_value(self, settings, configured, problem):
        settings.ISIK_NOTIFICATIONS = {"PROJECT_NAME": "Testapp", **configured}

        assert problem in setting_problems()

    def test_valid_edges(self, settings):
        settings.ISIK_NOTIFICATIONS = {
            "PROJECT_NAME": "Testapp",
            "RECORD_ATTEMPTS": "all",
            "MAX_ATTEMPTS": 1,
            "BACKOFF": (1,),
            "PARTITIONS": list,
            "IN_PARTITION": "contextlib.nullcontext",
        }

        assert setting_problems() == []

    def test_webhooks_need_a_signature_header(self, settings):
        settings.ISIK_NOTIFICATIONS = {}

        assert setting_problems() == [
            "The webhook channel signs with X-<PROJECT_NAME>-Signature - set ISIK_NOTIFICATIONS' PROJECT_NAME, "
            "or name the header in SIGNATURE['header']."
        ]

    def test_a_named_header_needs_no_project_name(self, settings):
        settings.ISIK_NOTIFICATIONS = {"SIGNATURE": {"header": "Webhook-Signature"}}

        assert setting_problems() == []

    def test_no_kind_sent_by_webhook_needs_no_header(self, settings, declaring):
        settings.ISIK_NOTIFICATIONS = {}
        declaring(shipped(channels=("inbox", "email")))

        assert setting_problems() == []
        assert "webhook" in conf.setting("CHANNELS")

    def test_set_nothing_at_all_with_no_webhook_kinds(self, settings, declaring):
        del settings.ISIK_NOTIFICATIONS
        declaring(shipped(channels=("inbox", "email")))

        assert notification_problems() == []

    @pytest.mark.parametrize("name", ["Acme Corp", "Açme", "Acme/Corp", "Acme:"])
    def test_a_project_name_a_header_cant_hold(self, settings, name):
        settings.ISIK_NOTIFICATIONS = {"PROJECT_NAME": name}

        assert setting_problems() == [
            f"ISIK_NOTIFICATIONS' PROJECT_NAME {name!r} can't be part of a header name - use letters, digits and -."
        ]

    @pytest.mark.parametrize("name", ["Acme", "acme-corp", "Acme.io", "A_1"])
    def test_a_project_name_that_fits(self, settings, name):
        settings.ISIK_NOTIFICATIONS = {"PROJECT_NAME": name}

        assert setting_problems() == []

    @pytest.mark.parametrize(
        ("push", "problems"),
        [
            ({}, ["A kind is sent on push, and ISIK_NOTIFICATIONS' PUSH holds no APNS or FCM credentials."]),
            ({"APNS": {}, "GCM": {}, "WNS": {}}, ["ISIK_NOTIFICATIONS' PUSH has keys it doesn't know: GCM, WNS."]),
            (
                {"APNS": {"TEAM_ID": "T", "KEY": ""}, "FCM": {}},
                [
                    "ISIK_NOTIFICATIONS' PUSH['APNS'] is missing KEY_ID, KEY, TOPIC.",
                    "ISIK_NOTIFICATIONS' PUSH['FCM'] is missing CREDENTIALS.",
                ],
            ),
            ({"APNS": {"TEAM_ID": "T", "KEY_ID": "K", "KEY": "pem", "TOPIC": "com.acme"}}, []),
            ({"FCM": {"CREDENTIALS": {"client_email": "x"}}}, []),
        ],
    )
    def test_push_needs_a_services_credentials(self, settings, declaring, push, problems):
        settings.ISIK_NOTIFICATIONS = {"CHANNELS": ("inbox", "push"), "PUSH": push}
        declaring(shipped(channels=("inbox", "push")))

        assert setting_problems() == problems

    def test_push_problems_are_told_beside_the_rest(self, settings, declaring):
        settings.ISIK_NOTIFICATIONS = {"CHANNELS": ("inbox", "push"), "RECORD_ATTEMPTS": "some"}
        declaring(shipped(channels=("inbox", "push")))

        assert setting_problems() == [
            "ISIK_NOTIFICATIONS' RECORD_ATTEMPTS is one of all, failures or none.",
            "A kind is sent on push, and ISIK_NOTIFICATIONS' PUSH holds no APNS or FCM credentials.",
        ]

    def test_no_kind_sent_by_push_needs_no_credentials(self, settings, declaring):
        settings.ISIK_NOTIFICATIONS = {"CHANNELS": ("inbox", "push"), "PUSH": {"GCM": {}}}
        declaring(shipped(channels=("inbox",)))

        assert setting_problems() == []

    def test_the_audience_without_the_ledger(self, monkeypatch):
        installed = {checks.AUDIENCE_APP}
        monkeypatch.setattr(apps, "is_installed", lambda name: name in installed)

        assert setting_problems() == [
            "isik.django.apps.notifications.audience is installed without isik.django.apps.notifications."
        ]


def shipped(**changes):
    return dataclasses.replace(kinds.get_kind("parcel_shipped"), **changes)


class Spanning(Audience):
    spans_partitions = True


class TestTheKinds:
    def test_one_declared_twice(self, declaring):
        reminder = kinds.get_kind("parcel_reminder")
        declaring(reminder, reminder, shipped(), shipped())

        assert kind_problems() == [
            "parcel_reminder is declared more than once.",
            "parcel_shipped is declared more than once.",
        ]

    def test_a_policy_not_a_policy(self, declaring):
        declaring(shipped(policy="sometimes"))

        assert kind_problems() == ["parcel_shipped's policy is a Policy, not 'sometimes'."]

    def test_a_push_carrying_what_the_snapshot_doesnt(self, declaring):
        reason = kinds.PushDataField(reason="the status opens the right tab in the app, and says nothing private")
        content = kinds.PushContent(data={"status": reason, "tracking_secret": reason, "weight": reason})
        declaring(shipped(push=content), dataclasses.replace(kinds.get_kind("parcel_reminder"), push=content))

        assert kind_problems() == ["parcel_shipped's push carries tracking_secret, weight, which its snapshot doesn't."]

    def test_a_single_partition_kind_whose_audience_spans_them(self, declaring):
        declaring(shipped(audience=Spanning()))

        assert kind_problems() == ["parcel_shipped is single-partition, and its audience Spanning spans partitions."]

    def test_a_cross_partition_kind_with_no_partitions(self, declaring, settings):
        declaring(shipped(scope=Scope.CROSS_PARTITION, audience=Spanning()))

        assert kind_problems() == ["parcel_shipped is cross-partition, and ISIK_NOTIFICATIONS declares no partitions."]
        settings.ISIK_NOTIFICATIONS = {
            "PARTITIONS": list,
            "IN_PARTITION": "contextlib.nullcontext",
            "DEFAULT_PARTITION": Connection(),
        }
        assert kind_problems() == []

    def test_a_channel_the_settings_dont_turn_on(self, declaring, settings):
        settings.ISIK_NOTIFICATIONS = {"PROJECT_NAME": "Testapp", "CHANNELS": ("inbox",)}
        declaring(shipped(channels=("inbox", "email", "webhook")))

        assert kind_problems() == [
            "parcel_shipped is sent on email, webhook, which ISIK_NOTIFICATIONS' CHANNELS doesn't turn on."
        ]

    def test_a_kind_on_a_ledger_or_event_model(self, declaring):
        declaring(
            shipped(model=Notice, snapshot=(), audience=Subscribers()),
            dataclasses.replace(shipped(), name="w", model=WidgetEvent, snapshot=(), audience=Subscribers()),
        )

        problems = kind_problems()
        assert (
            "parcel_shipped is declared on isik_notifications.Notice - a ledger or event model, which records "
            "changes rather than making them."
        ) in problems
        assert (
            "w is declared on testapp.WidgetEvent - a ledger or event model, which records changes rather than "
            "making them."
        ) in problems

    def test_a_snapshot_column_the_model_hasnt(self, declaring):
        declaring(shipped(snapshot=("status", "colour", "recipient")))

        assert kind_problems() == ["parcel_shipped's snapshot names colour, which testapp.Parcel has no column for."]

    def test_an_audience_reading_what_the_snapshot_doesnt_carry(self, declaring):
        declaring(shipped(snapshot=("status",), audience=Field("recipient", "sender")))

        assert kind_problems() == [
            "parcel_shipped's audience reads recipient, sender from its snapshot, which doesn't carry them - add "
            "them to snapshot, or read the live row with at=LIVE."
        ]

    def test_reading_the_live_row_needs_no_snapshot(self, declaring):
        from isik.django.apps.notifications.audiences import LIVE

        declaring(shipped(snapshot=("status",), audience=Field("recipient", at=LIVE)))

        assert kind_problems() == []

    def test_a_trigger_kind_with_no_trigger(self, declaring):
        declaring(dataclasses.replace(shipped(), name="unregistered"))

        assert kind_problems() == ["unregistered has no trigger registered."]

    def test_an_application_kind_isnt_read_for_a_model(self, declaring):
        declaring(Kind(name="app", audience=Field("x"), policy=Policy.MANDATORY, source=Source.APPLICATION))

        assert kind_problems() == []


@pytest.mark.django_db
class TestUnmirroredKinds:
    MESSAGE = (
        "{kind} is declared, and the database has no policy row for it - nothing writes its notices until migrate "
        "writes one, as the migrate this is shown by will."
    )

    @pytest.fixture
    def unmirrored(self):
        with pgtrigger.ignore("isik_notifications.NoticeKindPolicy:protect_policy_delete"):
            NoticeKindPolicy.objects.filter(name__in=["parcel_shipped", "parcel_flagged"]).delete()

    def test_a_declared_kind_with_no_row(self, unmirrored):
        NoticeKindPolicy.objects.filter(name="parcel_reminder").update(retired_at=timezone.now())

        assert checks.unmirrored_problems(["default"]) == [
            self.MESSAGE.format(kind="parcel_flagged"),
            self.MESSAGE.format(kind="parcel_shipped"),
        ]

    def test_every_one_mirrored_is_quiet(self):
        assert checks.unmirrored_problems(["default"]) == []

    def test_without_databases_nothing_is_read(self, unmirrored):
        assert checks.unmirrored_problems(None) == []

    def test_a_database_not_migrated_has_no_rows_to_miss(self, unmirrored, monkeypatch):
        def broken(*args, **kwargs):
            raise DatabaseError("no such table")

        monkeypatch.setattr(NoticeKindPolicy.objects, "using", broken)

        assert checks.unmirrored_problems(["default"]) == []

    def test_the_check_reports_them_as_warnings(self, unmirrored):
        messages = checks.notifications_can_work(app_configs=None, databases=["default"])

        hint = "See isik.django.apps.notifications."
        assert [(message.id, message.msg, message.hint) for message in messages if message.id == "isik.W009"] == [
            ("isik.W009", self.MESSAGE.format(kind="parcel_flagged"), hint),
            ("isik.W009", self.MESSAGE.format(kind="parcel_shipped"), hint),
        ]


@pytest.mark.django_db
class TestRetiredKinds:
    def test_a_declared_kind_the_database_retired(self):
        NoticeKindPolicy.objects.filter(name__in=["parcel_shipped", "parcel_flagged"]).update(retired_at=timezone.now())
        NoticeKindPolicy.objects.create(
            name="undeclared", policy="mandatory", scope="single_partition", source="trigger", retired_at=timezone.now()
        )

        assert retired_problems(["default"]) == [
            "parcel_flagged is declared, and retired - its trigger writes nothing. Remove its declaration.",
            "parcel_shipped is declared, and retired - its trigger writes nothing. Remove its declaration.",
        ]

    def test_without_databases_nothing_is_read(self):
        NoticeKindPolicy.objects.filter(name="parcel_shipped").update(retired_at=timezone.now())

        assert retired_problems(None) == []

    def test_a_database_not_migrated_has_none(self, monkeypatch):
        def broken(*args, **kwargs):
            raise DatabaseError("no such table")

        monkeypatch.setattr(NoticeKindPolicy.objects, "using", broken)

        assert retired_problems(["default"]) == []

    def test_the_check_reports_them_as_warnings(self, settings):
        NoticeKindPolicy.objects.filter(name="parcel_shipped").update(retired_at=timezone.now())
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "COLOR": "red"}

        messages = checks.notifications_can_work(app_configs=None, databases=["default"])

        assert [(message.id, message.msg) for message in messages] == [
            ("isik.E014", "ISIK_NOTIFICATIONS has keys it doesn't know: COLOR."),
            (
                "isik.W004",
                "parcel_shipped is declared, and retired - its trigger writes nothing. Remove its declaration.",
            ),
        ]
        assert {message.hint for message in messages} == {"See isik.django.apps.notifications."}


class TestTheReviewsChecks:
    def test_the_ledger_without_the_audience(self, monkeypatch):
        installed = {checks.LEDGER_APP}
        monkeypatch.setattr(apps, "is_installed", lambda name: name in installed)

        assert "isik.django.apps.notifications is installed without isik.django.apps.notifications.audience." in (
            setting_problems()
        )

    @pytest.mark.parametrize("allowed", ["10.0.0.0/8", ["not a network"], 3])
    def test_private_targets_that_arent_networks(self, settings, allowed):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "ALLOW_PRIVATE_TARGETS": allowed}

        assert setting_problems() == [
            'ISIK_NOTIFICATIONS\' ALLOW_PRIVATE_TARGETS is True, False, or networks such as "10.1.0.0/16".'
        ]

    @pytest.mark.parametrize("allowed", [True, False, ["10.1.0.0/16", "fd00::/8"], ()])
    def test_private_targets_that_are(self, settings, allowed):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "ALLOW_PRIVATE_TARGETS": allowed}

        assert setting_problems() == []

    def test_an_email_kind_without_its_templates(self, declaring):
        declaring(dataclasses.replace(shipped(), name="no_templates"))

        assert checks._template_problems() == [
            "no_templates is sent by email, and there's no template notifications/no_templates/subject.txt.",
            "no_templates is sent by email, and there's no template notifications/no_templates/body.txt.",
        ]

    def test_a_kind_not_sent_by_email_needs_none(self, declaring):
        declaring(dataclasses.replace(shipped(), name="no_templates", channels=("inbox",)))

        assert checks._template_problems() == []

    def test_a_sender_of_the_projects_needs_none(self, declaring, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "EMAIL_SENDER": print}
        declaring(dataclasses.replace(shipped(), name="no_templates"))

        assert checks._template_problems() == []

    def test_the_default_sender_without_djangos_template_backend(self, settings):
        settings.TEMPLATES = [{"BACKEND": "django.template.backends.jinja2.Jinja2", "DIRS": [], "APP_DIRS": False}]

        assert checks._template_problems() == [
            "The default EMAIL_SENDER renders its templates with Django's template backend, which TEMPLATES doesn't "
            "include - add it, or set EMAIL_SENDER to a sender of your own."
        ]

    def test_an_exemption_for_a_field_the_snapshot_doesnt_carry(self, declaring):
        from isik.django.apps.notifications.kinds import SensitiveSnapshotField

        declaring(
            shipped(
                sensitive={
                    "tracking_secret": SensitiveSnapshotField(
                        reason="shown to the parcel's sender alone, and never mailed"
                    )
                }
            )
        )

        assert kind_problems() == [
            "parcel_shipped says why its snapshot carries tracking_secret, which it doesn't carry."
        ]


class TestSecretLookingSnapshots:
    def test_each_field_that_looks_secret_is_warned_about(self, declaring):
        declaring(shipped(snapshot=("status", "tracking_secret", "api_token", "password_hint")))

        assert checks.sensitive_snapshot_warnings() == [
            f"parcel_shipped's snapshot carries {name}, which looks secret - the snapshot is copied into the ledger "
            f"and every channel. Leave it out, or say why with "
            f"sensitive={{{name!r}: SensitiveSnapshotField(reason=...)}}."
            for name in ("tracking_secret", "api_token", "password_hint")
        ]

    def test_one_with_its_reason_isnt(self, declaring):
        from isik.django.apps.notifications.kinds import SensitiveSnapshotField

        reason = SensitiveSnapshotField(reason="only the last four characters, shown to the owner")
        declaring(shipped(snapshot=("status", "tracking_secret"), sensitive={"tracking_secret": reason}))

        assert checks.sensitive_snapshot_warnings() == []

    def test_an_application_kind_carries_what_its_raiser_gives_it(self, declaring):
        declaring(Kind(name="app", audience=Subscribers(), policy=Policy.MANDATORY, source=Source.APPLICATION))

        assert checks.sensitive_snapshot_warnings() == []

    def test_the_check_warns(self, declaring):
        declaring(shipped(snapshot=("status", "recipient", "tracking_secret")))

        assert [message.id for message in checks.notifications_can_work(app_configs=None)] == ["isik.W005"]

    def test_a_reason_is_a_typed_exemption_never_a_string(self):
        from django.core.exceptions import ImproperlyConfigured

        from isik.django.apps.notifications.kinds import notifies

        with pytest.raises(ImproperlyConfigured, match="takes a SensitiveSnapshotField"):
            notifies(
                "k",
                audience=Subscribers(),
                policy=Policy.MANDATORY,
                on=pgtrigger.Update,
                sensitive={"secret": "it's fine"},
            )


@pytest.mark.django_db
def test_a_kind_on_the_user_model_carries_no_password_unless_named(alice):
    from isik.django.apps.notifications.models import Notice

    # The test project's kinds name their snapshot; none names a password, so no notice ever holds one.
    for notice in Notice.objects.all():
        assert "password" not in notice.snapshot
    assert all("password" not in kind.snapshot for kind in kinds.kinds().values())


class TestTheVocabulary:
    def test_the_test_projects_agrees_with_what_it_writes(self):
        assert checks._vocabulary_problems() == []

    def test_a_declared_kind_nothing_writes(self, monkeypatch):
        monkeypatch.setattr(kinds, "vocabulary", [*kinds.vocabulary, kinds.KindName("orphan")])

        assert checks._vocabulary_problems() == [
            "orphan is declared, and nothing writes it - give a model @notifies(orphan, ...), or declare_kind() its "
            "audience and policy."
        ]

    def test_variants_written_that_arent_declared_or_the_reverse(self, monkeypatch):
        others = [name for name in kinds.vocabulary if name != "parcel_flagged"]
        monkeypatch.setattr(kinds, "vocabulary", [*others, kinds.KindName("parcel_flagged", ("lost", "stolen"))])

        assert checks._vocabulary_problems() == [
            "parcel_flagged writes the variants damaged, lost, and declares lost, stolen."
        ]

    def test_a_kind_with_variants_declared_writing_none(self, monkeypatch):
        others = [name for name in kinds.vocabulary if name != "parcel_shipped"]
        monkeypatch.setattr(kinds, "vocabulary", [*others, kinds.KindName("parcel_shipped", ("x",))])

        assert checks._vocabulary_problems() == ["parcel_shipped writes the variants none, and declares x."]

    def test_a_name_declared_twice(self, monkeypatch):
        monkeypatch.setattr(kinds, "vocabulary", [*kinds.vocabulary, kinds.KindName("parcel_shipped")])

        assert checks._vocabulary_problems() == ["parcel_shipped is declared in the vocabulary more than once."]

    def test_its_problems_are_the_kinds(self, monkeypatch):
        monkeypatch.setattr(kinds, "vocabulary", [*kinds.vocabulary, kinds.KindName("orphan")])

        assert kind_problems()[0].startswith("orphan is declared, and nothing writes it")


class TestRequiringDeclaredKinds:
    def test_off_a_bare_name_is_taken(self):
        assert checks._undeclared_problems() == []

    def test_on_a_bare_name_is_refused(self, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "REQUIRE_DECLARED_KINDS": True}

        assert checks._undeclared_problems() == [
            "parcel_routed is a bare name - declare it with declare_kind() in your kinds module, and pass that."
        ]
        assert "parcel_routed is a bare name" in kind_problems()[0]


class TestEndpointOwners:
    URLS = "tests.django.apps.notifications.api.urls"

    def test_a_view_scoping_by_an_owner_the_channel_doesnt(self):
        assert checks.endpoint_owner_problems(self.URLS) == [
            f"{view} scopes endpoints by 'url', and ISIK_NOTIFICATIONS' ENDPOINT_OWNER_FIELD is None - set it to "
            "'url', or each endpoint is sent notices its owner isn't told of."
            for view in ("OwnedEndpointViewSet", "OwnedDeliveryViewSet")
        ]

    def test_the_same_owner_on_both(self, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "ENDPOINT_OWNER_FIELD": "url"}

        assert checks.endpoint_owner_problems(self.URLS) == []

    def test_without_rest_framework_theres_no_view(self, monkeypatch):
        monkeypatch.setattr(apps, "is_installed", lambda name: name != "rest_framework")

        assert checks.endpoint_owner_problems(self.URLS) == []

    def test_the_check_reports_them_over_its_urlconfs(self, monkeypatch, settings):
        settings.ISIK_CHECKS = {Check.NOTIFICATIONS: {CheckOption.URLCONFS: [self.URLS]}}
        monkeypatch.setattr(checks, "endpoint_owner_problems", lambda urlconf: [f"scoped in {urlconf}"])

        messages = checks.notifications_can_work(app_configs=None)

        assert ("isik.E014", f"scoped in {[self.URLS]}", "See isik.django.apps.notifications.") in [
            (message.id, message.msg, message.hint) for message in messages
        ]


class TestSharedEndpoints:
    WARNING = (
        "testapp.Parcel is keyed to the user model by sender, recipient, and ISIK_NOTIFICATIONS' "
        "ENDPOINT_OWNER_FIELD is unset - so every endpoint is sent every user's notices of its kinds. Set it to "
        "the owner's key, or say why they're the deployment's with ENDPOINTS_SHARED=SharedEndpoints(reason=...)."
    )

    @pytest.fixture
    def keyed_to_users(self, settings):
        # Parcel stands in for an endpoint model a project owns, with keys to the user model.
        settings.ISIK_NOTIFICATIONS_ENDPOINT_MODEL = "testapp.Parcel"
        return settings

    def test_isiks_own_endpoint_names_no_user(self):
        assert checks.shared_endpoint_warnings() == []

    def test_an_endpoint_keyed_to_users(self, keyed_to_users):
        assert checks.shared_endpoint_warnings() == [self.WARNING]
        assert ("isik.W008", self.WARNING, "See isik.django.apps.notifications.") in [
            (message.id, message.msg, message.hint) for message in checks.notifications_can_work(app_configs=None)
        ]

    def test_not_once_its_owner_is_named(self, keyed_to_users):
        keyed_to_users.ISIK_NOTIFICATIONS = {**keyed_to_users.ISIK_NOTIFICATIONS, "ENDPOINT_OWNER_FIELD": "sender"}

        assert checks.shared_endpoint_warnings() == []

    def test_not_once_a_reason_says_theyre_shared(self, keyed_to_users):
        keyed_to_users.ISIK_NOTIFICATIONS = {
            **keyed_to_users.ISIK_NOTIFICATIONS,
            "ENDPOINTS_SHARED": SharedEndpoints(reason="sender is who registered it, an audit column, not an owner"),
        }

        assert checks.shared_endpoint_warnings() == []
        assert checks.setting_problems() == []

    def test_not_without_the_audience_app(self, keyed_to_users, monkeypatch):
        monkeypatch.setattr(apps, "is_installed", lambda name: name != checks.AUDIENCE_APP)

        assert checks.shared_endpoint_warnings() == []

    def test_a_reason_is_typed(self, keyed_to_users):
        keyed_to_users.ISIK_NOTIFICATIONS = {**keyed_to_users.ISIK_NOTIFICATIONS, "ENDPOINTS_SHARED": "they're ours"}

        assert checks.setting_problems() == [
            "ISIK_NOTIFICATIONS.ENDPOINTS_SHARED takes a SharedEndpoints(reason=...), not a bare string."
        ]


def test_a_channel_that_cant_be_loaded_is_found_at_startup(settings, monkeypatch):
    monkeypatch.setitem(registered, "fax", "tests.nowhere.FaxChannel")
    settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "CHANNELS": ("inbox", "fax")}

    assert checks.setting_problems() == [
        "ISIK_NOTIFICATIONS' CHANNELS turns on fax, which can't be loaded: No module named 'tests.nowhere'"
    ]


def test_a_channel_registered_by_class_is_loaded_too(settings, monkeypatch):
    from isik.django.apps.notifications.channels.inbox import InboxChannel

    monkeypatch.setitem(registered, "fax", InboxChannel)
    settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "CHANNELS": ("inbox", "fax")}

    assert checks.setting_problems() == []
