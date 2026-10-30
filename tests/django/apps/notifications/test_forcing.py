"""Forceable subscriptions, the force sources behind them, and what a kind says to people."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, models, transaction

from isik.django.apps.common import forcing
from isik.django.apps.common.forcing import ForceRefused, checked_force, checked_release, get_force_source
from isik.django.apps.notifications import checks, kinds, pipeline
from isik.django.apps.notifications.audience.bases import _force_constraints
from isik.django.apps.notifications.audience.helpers import (
    KindIsMandatory,
    SubscriptionForced,
    SubscriptionRefused,
    force_subscription,
    release_subscription,
    subscribe,
    unsubscribe,
)
from isik.django.apps.notifications.kinds import KindText, rendered, text_of
from isik.django.apps.notifications.models import Notice, NoticeKindPolicy
from tests.testapp.models import ForceableSubscription


pytestmark = pytest.mark.django_db

REASON = "the person asked support to keep these on while they're travelling"


def row(alice, kind="parcel_flagged", variant=None):
    return ForceableSubscription.objects.get(user=alice, kind_id=kind, variant=variant)


class TestForceSources:
    def test_a_source_is_declared_once(self):
        with pytest.raises(ValueError, match="^A force source called staff is declared already.$"):
            forcing.declare_force_source("staff")

    def test_an_undeclared_source_is_refused(self):
        with pytest.raises(ForceRefused, match="^No force source is declared as nobody.$"):
            get_force_source("nobody")

    def test_a_reason_reaches_its_sources_floor(self):
        assert checked_force("automated", "fixed").name == "automated"
        with pytest.raises(ForceRefused, match="^A force by Staff needs a reason of at least 40 characters.$"):
            checked_force("staff", "short")
        with pytest.raises(ForceRefused):
            checked_force("staff", None)

    def test_staff_lift_only_what_a_source_lets_them(self):
        assert checked_release("staff", by_operator=True).name == "staff"
        assert checked_release("account_review", by_operator=False).name == "account_review"
        with pytest.raises(ForceRefused, match="^What Account review forced can only be lifted by it, not by staff.$"):
            checked_release("account_review", by_operator=True)

    def test_a_source_with_no_label_is_named(self, monkeypatch):
        monkeypatch.setitem(forcing._declared, "unnamed", forcing.ForceSource("unnamed", operator_may_release=False))

        with pytest.raises(ForceRefused, match="^A force by unnamed needs a reason of at least 40 characters.$"):
            checked_force("unnamed", "short")
        with pytest.raises(ForceRefused, match="^What unnamed forced can only be lifted by it, not by staff.$"):
            checked_release("unnamed", by_operator=True)

    def test_every_source_is_listed(self):
        assert list(forcing.force_sources())[:3] == ["staff", "account_review", "automated"]
        assert str(get_force_source("staff")) == "staff"


class TestTheDefaultModel:
    def test_a_mandatory_kind_is_no_ones_to_choose(self, alice):
        with pytest.raises(KindIsMandatory, match="^subscription_forced is mandatory - it reaches everyone it's for.$"):
            subscribe(alice, "subscription_forced")
        assert KindIsMandatory.code == "kind_mandatory"

    def test_it_cant_be_forced(self, alice):
        with pytest.raises(
            ImproperlyConfigured,
            match=(
                "^isik_notifications_audience.Subscription can't be forced - base it on AbstractForceableSubscription.$"
            ),
        ):
            force_subscription(alice, "parcel_flagged", enabled=True, source="staff", reason=REASON)

    def test_choosing_works_as_before(self, alice):
        subscribe(alice, "parcel_flagged")

        assert unsubscribe(alice, "parcel_flagged") == 1


class TestForcing:
    def test_a_force_with_no_choice_of_their_own(self, forceable, alice):
        forced = force_subscription(alice, "parcel_flagged", enabled=True, source="staff", reason=REASON)

        assert (forced.chosen, forced.forced, forced.forced_enabled, forced.forced_source) == (
            False,
            True,
            True,
            "staff",
        )
        assert forced.forced_reason == REASON

    def test_a_force_keeps_their_own_choice_and_its_release_brings_it_back(self, forceable, alice):
        subscribe(alice, "parcel_flagged", enabled=True)

        force_subscription(alice, "parcel_flagged", enabled=False, source="staff", reason=REASON)
        assert (row(alice).enabled, row(alice).forced_enabled, row(alice).chosen) == (True, False, True)

        assert release_subscription(alice, "parcel_flagged") is True
        released = row(alice)
        assert (released.enabled, released.forced, released.forced_enabled) == (True, False, None)
        assert (released.forced_source, released.forced_reason) == ("", "")

    def test_releasing_a_force_with_no_choice_behind_it_leaves_nothing(self, forceable, alice):
        force_subscription(alice, "parcel_flagged", enabled=True, source="staff", reason=REASON)

        release_subscription(alice, "parcel_flagged")

        assert not forceable.exists()

    def test_releasing_what_isnt_forced(self, forceable, alice, bob):
        # Another's force, a force of one variant, and their own unforced choice: none is this one to release.
        force_subscription(bob, "parcel_flagged", enabled=True, source="staff", reason=REASON)
        force_subscription(alice, "parcel_flagged", "lost", enabled=True, source="staff", reason=REASON)
        subscribe(alice, "parcel_shipped")

        assert release_subscription(alice, "parcel_flagged") is False
        assert release_subscription(alice, "parcel_shipped") is False
        assert release_subscription(alice, "parcel_flagged", "damaged") is False
        assert sorted(forceable.filter(forced=True).values_list("user", "variant")) == [
            (alice.pk, "lost"),
            (bob.pk, None),
        ]

    def test_releasing_another_kinds_force(self, forceable, alice):
        force_subscription(alice, "parcel_shipped", enabled=False, source="staff", reason=REASON)

        assert release_subscription(alice, "parcel_flagged") is False
        assert row(alice, "parcel_shipped").forced

    def test_a_variant_forced_over_their_own_choice_of_it(self, forceable, alice):
        chosen = subscribe(alice, "parcel_flagged", "lost", enabled=False)

        forced = force_subscription(alice, "parcel_flagged", "lost", enabled=True, source="staff", reason=REASON)

        assert forced.pk == chosen.pk
        assert (forced.chosen, forced.enabled, forced.forced_enabled) == (True, False, True)

    def test_forcing_it_again_replaces_the_force(self, forceable, alice):
        force_subscription(alice, "parcel_flagged", enabled=True, source="staff", reason=REASON)

        force_subscription(alice, "parcel_flagged", enabled=False, source="automated", reason="rotated")

        assert (row(alice).forced_enabled, row(alice).forced_source, row(alice).chosen) == (False, "automated", False)

    def test_forcing_the_kind_drops_their_own_variant_choices(self, forceable, alice):
        subscribe(alice, "parcel_flagged", "lost")

        force_subscription(alice, "parcel_flagged", enabled=True, source="staff", reason=REASON)

        assert list(forceable.values_list("variant", flat=True)) == [None]

    def test_forcing_the_kind_over_a_forced_variant_is_refused(self, forceable, alice):
        force_subscription(alice, "parcel_flagged", "lost", enabled=True, source="staff", reason=REASON)

        with pytest.raises(SubscriptionForced):
            force_subscription(alice, "parcel_flagged", enabled=True, source="staff", reason=REASON)

    def test_forcing_a_variant_beside_their_choice_for_every_one_is_refused(self, forceable, alice):
        subscribe(alice, "parcel_flagged")

        with pytest.raises(
            SubscriptionRefused,
            match=f"^{alice} has a choice about every variant of parcel_flagged - force the kind, not one variant.$",
        ):
            force_subscription(alice, "parcel_flagged", "lost", enabled=True, source="staff", reason=REASON)

    def test_a_mandatory_kind_isnt_forced(self, forceable, alice):
        with pytest.raises(KindIsMandatory):
            force_subscription(alice, "subscription_forced", enabled=False, source="staff", reason=REASON)

    def test_a_source_staff_cant_lift(self, forceable, alice):
        force_subscription(alice, "parcel_flagged", enabled=True, source="account_review", reason=REASON)

        with pytest.raises(ForceRefused):
            release_subscription(alice, "parcel_flagged")
        assert release_subscription(alice, "parcel_flagged", by_operator=False) is True


class TestAForcedRowCantBeChanged:
    @pytest.fixture
    def forced(self, forceable, alice):
        return force_subscription(alice, "parcel_flagged", enabled=True, source="staff", reason=REASON)

    def test_by_subscribing(self, forced, alice):
        with pytest.raises(SubscriptionForced, match="^This subscription is forced by staff, so it can't be changed.$"):
            subscribe(alice, "parcel_flagged", enabled=False)
        assert SubscriptionForced.code == "subscription_forced"

    def test_by_subscribing_to_every_variant_over_a_forced_one(self, forceable, alice):
        force_subscription(alice, "parcel_flagged", "lost", enabled=True, source="staff", reason=REASON)

        with pytest.raises(SubscriptionForced):
            subscribe(alice, "parcel_flagged", enabled=False)
        assert row(alice, variant="lost").forced

    def test_by_subscribing_to_one_of_its_variants(self, forceable, alice):
        force_subscription(alice, "parcel_flagged", "lost", enabled=True, source="staff", reason=REASON)

        with pytest.raises(SubscriptionForced):
            subscribe(alice, "parcel_flagged", "lost", enabled=False)
        subscribe(alice, "parcel_flagged", "damaged")

    def test_by_unsubscribing(self, forced, alice):
        with pytest.raises(SubscriptionForced):
            unsubscribe(alice, "parcel_flagged")

    def test_in_the_database(self, forceable, alice):
        with transaction.atomic(), pytest.raises(IntegrityError, match="_forced_exactly"):
            forceable.create(user=alice, kind_id="parcel_flagged", forced=True, forced_source="staff")
        with transaction.atomic(), pytest.raises(IntegrityError, match="_chosen_or_forced"):
            forceable.create(user=alice, kind_id="parcel_flagged", chosen=False)


class TestTellingThePerson:
    def test_a_force_and_its_release_are_told(self, forceable, alice, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "SUBSCRIPTION_FORCED_KIND": "subscription_forced"}

        force_subscription(alice, "parcel_flagged", "lost", enabled=False, source="staff", reason=REASON)
        release_subscription(alice, "parcel_flagged", "lost")

        assert [notice.snapshot for notice in Notice.objects.filter(kind_id="subscription_forced").order_by("pk")] == [
            {"user": alice.pk, "kind": "parcel_flagged", "variant": "lost", "forced": True, "enabled": False},
            {"user": alice.pk, "kind": "parcel_flagged", "variant": "lost", "forced": False, "enabled": None},
        ]

    def test_not_unless_a_kind_is_named(self, forceable, alice):
        force_subscription(alice, "parcel_flagged", enabled=False, source="staff", reason=REASON)

        assert not Notice.objects.filter(kind_id="subscription_forced").exists()


class TestWhoGetsIt:
    def users(self, notice_kind, *users):
        kind = kinds.get_kind(notice_kind)
        notice = Notice(kind_id=notice_kind, variant="lost", snapshot={})
        audience = type("Them", (), {"recipients": lambda self, notice: list(users)})()
        return pipeline.chosen_users(notice, kinds.Kind(name=kind.name, audience=audience, policy=kind.policy))

    def test_a_force_beats_the_default_and_their_own_choice(self, forceable, alice, bob):
        subscribe(bob, "parcel_flagged")
        force_subscription(alice, "parcel_flagged", enabled=True, source="staff", reason=REASON)
        force_subscription(bob, "parcel_flagged", enabled=False, source="staff", reason=REASON)

        assert self.users("parcel_flagged", alice, bob) == [alice]

    def test_their_own_choice_and_the_default(self, forceable, alice, bob):
        subscribe(alice, "parcel_shipped", enabled=False)

        assert self.users("parcel_shipped", alice, bob) == [bob]
        assert self.users("parcel_flagged", alice, bob) == []


class TestWhatAKindSays:
    def test_its_declaration_over_its_vocabulary_entry(self):
        assert text_of("parcel_flagged").title == "A parcel was %(status)s"
        assert text_of("parcel_routed") == KindText(label="Parcel routed", message="A parcel was routed.")
        assert text_of("nothing") == KindText()

    def test_rendered_from_the_data(self):
        assert rendered("Your parcel is %(status)s.", {"status": "late"}, None) == "Your parcel is late."
        assert rendered(lambda data, variant: f"{len(data)} things, {variant}", {"a": 1}, "lost") == "1 things, lost"
        assert rendered(None, {}, None) is None
        assert rendered("Missing %(nothing)s.", {}, None) is None
        assert rendered("Odd %(n)d.", {"n": "x"}, None) is None

    def test_merging_fills_only_what_was_left_out(self):
        assert KindText(label="Mine").merged_over(KindText(label="Theirs", message="Hi")) == KindText(
            label="Mine", message="Hi"
        )


class TestTheChecks:
    def test_a_kind_without_a_label_or_its_inbox_message(self, declaring):
        from isik.django.apps.notifications.audiences import Subscribers

        declaring(
            kinds.Kind(
                name="bare", audience=Subscribers(), policy="mandatory", source="application", channels=("inbox",)
            ),
            kinds.Kind(
                name="mailed", audience=Subscribers(), policy="mandatory", source="application", channels=("email",)
            ),
        )

        assert checks.kind_text_problems() == [
            "bare has no label - a client listing kinds can only show its name. Give it label=.",
            "bare is sent to the inbox with no message - its items read as data. Give it message=.",
            "mailed has no label - a client listing kinds can only show its name. Give it label=.",
        ]

    def test_the_check_reports_them(self, monkeypatch):
        from isik.django.apps.common import checks as common_checks

        monkeypatch.setattr(checks, "kind_text_problems", lambda: ["one"])

        errors = common_checks.every_declaration_says_what_it_is(app_configs=None)

        assert [(error.id, error.msg) for error in errors] == [("isik.E016", "one")]
        assert errors[0].hint == "Declare a label (and a message, for a kind sent to the inbox) - see KindText."

    def test_the_test_project_says_what_each_kind_is(self):
        assert checks.kind_text_problems() == []

    def test_a_forceable_model_needs_its_history(self, settings, monkeypatch):
        settings.ISIK_NOTIFICATIONS_SUBSCRIPTION_MODEL = "testapp.ForceableSubscription"
        assert checks._untracked_force_problems() == []
        monkeypatch.delattr(ForceableSubscription, "pgh_event_model")

        assert checks._untracked_force_problems() == [
            "testapp.ForceableSubscription can be forced, and has no history - who forced it, when and why is gone "
            "once it's released. Track it with @track_events()."
        ]

    def test_the_default_model_needs_none(self):
        assert checks._untracked_force_problems() == []

    def test_a_policy_row_is_read_for_mandatory(self, alice):
        NoticeKindPolicy.objects.filter(pk="parcel_flagged").update(policy="mandatory")

        with pytest.raises(KindIsMandatory):
            subscribe(alice, "parcel_flagged")


def test_a_forceable_subscription_is_held_to_its_forcing_by_two_constraints():
    table = ForceableSubscription._meta.db_table
    unforced = models.Q(forced=False, forced_enabled__isnull=True, forced_source="", forced_reason="")
    forced = models.Q(forced=True, forced_enabled__isnull=False) & ~models.Q(forced_source="")

    assert _force_constraints(ForceableSubscription) == [
        models.CheckConstraint(condition=unforced | forced, name=f"{table}_forced_exactly"),
        models.CheckConstraint(
            condition=models.Q(chosen=True) | models.Q(forced=True), name=f"{table}_chosen_or_forced"
        ),
    ]
