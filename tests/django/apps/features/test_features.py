"""Features, grants and overrideable settings: declaring, resolving, writing, the sweep and erasing a holder."""

import gc
from datetime import timedelta

import pytest
from celery import Celery
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, ProgrammingError, connection, models, transaction
from django.db.models import ProtectedError
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from isik.django.apps.common.forcing import ForceRefused
from isik.django.apps.features import declarations, operations, resolution, schedule, swapping, tasks
from isik.django.apps.features.declarations import AtLeast, AtMost, Default, Fixed, Setting
from isik.django.apps.features.guards import FeatureMissing, requires_feature
from isik.django.apps.features.models import Feature, FeatureGrant, SweepCursor
from isik.django.apps.features.operations import (
    GrantRefused,
    SettingForced,
    SettingInvalid,
    SettingLocked,
    SettingRefused,
    erase_holder,
    force_setting,
    grant,
    release_everything_forced_by,
    release_setting,
    revoke,
    set_setting,
    sweep_boundaries,
)
from isik.django.apps.features.resolution import Effective, effective, effective_expression, has_feature, held_features
from tests.testapp.models import EmailUser, UserOverrides


pytestmark = pytest.mark.django_db

REASON = "the account is under investigation and stays short-lived until it's done"


class TestDeclaring:
    def test_a_feature_or_setting_is_declared_once(self):
        with pytest.raises(ImproperlyConfigured, match="^A feature called webhooks is declared already.$"):
            declarations.declare_feature("webhooks")
        with pytest.raises(ImproperlyConfigured, match="^A setting called theme is declared already.$"):
            declarations.declare_setting("theme", models.BooleanField(), default=False)

    def test_a_setting_needs_a_feature_that_is_declared(self):
        with pytest.raises(ImproperlyConfigured, match="^nothing needs the feature ghost, which isn't declared.$"):
            declarations.declare_setting("nothing", models.BooleanField(), default=False, feature="ghost")

    def test_each_setting_has_a_type(self):
        assert {name: setting.type for name, setting in declarations.settings.items()} == {
            "max_endpoints": "int",
            "token_lifetime_days": "int",
            "two_factor_required": "bool",
            "theme": "choice",
            "session_length": "duration",
            "display_name": "text",
            "staff_note": "text",
        }
        assert Setting("x", models.FloatField(), 0).type == "float"
        assert Setting("x", models.DecimalField(), 0).type == "decimal"
        assert Setting("x", models.JSONField(), 0).type == "text"

    def test_what_a_feature_unlocks(self):
        assert declarations.settings_unlocked_by("long_lived_tokens") == ["token_lifetime_days"]
        assert declarations.settings_unlocked_by("beta") == []

    def test_a_feature_name_carries_its_text(self):
        assert (declarations.features["webhooks"].label, declarations.features["internal_flag"].visible_to_holder) == (
            "Webhooks",
            False,
        )

    def test_bounds(self):
        assert (Default().locked(3), Fixed(0).locked(3)) == (3, 0)
        assert (AtMost(90).apply(120), AtMost(90).apply(60), AtLeast(5).apply(2), AtLeast(5).apply(9)) == (90, 60, 5, 9)
        assert Default() == Default()


class TestResolving:
    def test_the_default_until_the_holder_chooses(self, alice):
        assert effective(alice, "theme") == Effective("light", "default")

    def test_their_own_choice(self, alice, fresh):
        set_setting(alice, "theme", "dark")

        assert effective(fresh(alice), "theme") == Effective("dark", "own")

    def test_a_force_wins_and_its_release_brings_their_own_back(self, alice, fresh):
        grant(alice, "long_lived_tokens", source="manual")
        set_setting(alice, "token_lifetime_days", 60)

        force_setting(alice, "token_lifetime_days", 30, source="staff", reason=REASON)
        assert effective(fresh(alice), "token_lifetime_days") == Effective(30, "forced", "staff", REASON)

        assert release_setting(alice, "token_lifetime_days") is True
        assert effective(fresh(alice), "token_lifetime_days") == Effective(60, "own")

    def test_releasing_what_isnt_forced(self, alice, bob, fresh):
        # Another holder's force, and a row of their own that forces nothing: neither is theirs to release.
        force_setting(bob, "theme", "dark", source="account_review", reason=REASON)
        set_setting(alice, "theme", "dark")

        assert release_setting(alice, "theme") is False
        assert effective(fresh(bob), "theme").origin == "forced"

    def test_a_missing_feature_gives_the_default(self, alice):
        assert effective(alice, "max_endpoints") == Effective(0, "feature_missing")

    def test_a_missing_feature_clamps_a_bounded_one(self, alice, fresh):
        set_setting(alice, "token_lifetime_days", 120)
        assert effective(fresh(alice), "token_lifetime_days") == Effective(90, "limited")

        set_setting(alice, "token_lifetime_days", 60)
        assert effective(fresh(alice), "token_lifetime_days") == Effective(60, "own")
        assert effective(fresh(EmailUser.objects.create(username="c", email="c@x.com")), "token_lifetime_days") == (
            Effective(30, "default")
        )

    def test_the_feature_lifts_the_clamp(self, alice, fresh):
        set_setting(alice, "token_lifetime_days", 120)

        grant(alice, "long_lived_tokens", source="trial")

        assert effective(fresh(alice), "token_lifetime_days") == Effective(120, "own")

    def test_an_own_value_on_the_holder(self, alice, fresh):
        assert effective(alice, "display_name") == Effective("Alice", "own")

        set_setting(alice, "display_name", "Al")

        assert EmailUser.objects.get(pk=alice.pk).first_name == "Al"
        assert effective(fresh(alice), "display_name").value == "Al"

    def test_a_holders_reads_are_cached_until_forgotten(self, alice, django_assert_num_queries):
        effective(alice, "max_endpoints")

        with django_assert_num_queries(0):
            assert has_feature(alice, "webhooks") is False
            effective(alice, "theme")
        resolution.forget(alice)
        with django_assert_num_queries(2):
            effective(alice, "max_endpoints")

    def test_a_setting_needing_no_feature_asks_nothing_of_the_grants(self, alice, django_assert_num_queries):
        with django_assert_num_queries(1):
            assert effective(alice, "theme") == Effective("light", "default")
        with django_assert_num_queries(1):
            assert has_feature(alice, "webhooks") is False

    def test_a_forced_setting_asks_nothing_of_the_grants(self, alice, fresh, django_assert_num_queries):
        force_setting(alice, "max_endpoints", 2, source="staff", reason=REASON)
        alice = fresh(alice)

        with django_assert_num_queries(1):
            assert effective(alice, "max_endpoints").value == 2

    def test_an_overrides_row_loaded_with_the_holder_is_used(self, alice, bob, django_assert_num_queries):
        set_setting(alice, "theme", "dark")

        with django_assert_num_queries(1):
            loaded = {user.pk: user for user in EmailUser.objects.select_related("setting_overrides")}
            assert effective(loaded[alice.pk], "theme") == Effective("dark", "own")
            assert effective(loaded[bob.pk], "theme") == Effective("light", "default")

    def test_grants_loaded_with_some_other_relation_are_asked_for(self, alice, django_assert_num_queries):
        grant(alice, "webhooks", source="manual")
        (loaded,) = EmailUser.objects.prefetch_related("groups")

        with django_assert_num_queries(1):
            assert has_feature(loaded, "webhooks") is True

    def test_grants_loaded_with_the_holder_are_used_in_force_alone(self, monkeypatch, django_assert_num_queries):
        now = timezone.now()
        terms = {
            "starts_now": dict(starts_at=now),
            "ends_later": dict(starts_at=now - timedelta(days=1), expires_at=now + timedelta(days=1)),
            "starts_later": dict(starts_at=now + timedelta(days=1)),
            "ended_now": dict(starts_at=now - timedelta(days=1), expires_at=now),
            "revoked_now": dict(starts_at=now - timedelta(days=1), revoked_at=now),
            "revoked_later": dict(starts_at=now - timedelta(days=1), revoked_at=now + timedelta(days=1)),
        }
        holders = {}
        for name, made in terms.items():
            holders[name] = EmailUser.objects.create(username=name, email=f"{name}@example.com")
            FeatureGrant.objects.create(holder=holders[name], feature_id="webhooks", source="manual", **made)
        loaded = {name: with_grants_loaded(EmailUser.objects.get(pk=holder.pk)) for name, holder in holders.items()}
        monkeypatch.setattr(timezone, "now", lambda: now)

        with django_assert_num_queries(0):
            holding = {name: has_feature(holder, "webhooks") for name, holder in loaded.items()}

        assert holding == {
            "starts_now": True,
            "ends_later": True,
            "starts_later": False,
            "ended_now": False,
            "revoked_now": False,
            "revoked_later": True,
        }

    def test_a_write_forgets_what_was_loaded_with_the_holder(self, alice):
        set_setting(alice, "theme", "dark")
        (loaded,) = EmailUser.objects.filter(pk=alice.pk).select_related("setting_overrides")
        with_grants_loaded(loaded)
        assert (has_feature(loaded, "webhooks"), effective(loaded, "theme").value) == (False, "dark")

        grant(loaded, "webhooks", source="manual")
        set_setting(loaded, "theme", "light")

        assert (has_feature(loaded, "webhooks"), effective(loaded, "theme").value) == (True, "light")
        assert list(loaded._prefetched_objects_cache) == ["groups"]

    def test_an_unsaved_holder_holds_nothing_and_has_no_overrides(self, django_assert_num_queries):
        unsaved = EmailUser(first_name="Una")

        with django_assert_num_queries(0):
            assert effective(unsaved, "display_name") == Effective("Una", "own")
            assert effective(unsaved, "token_lifetime_days") == Effective(30, "default")
            assert held_features(unsaved) == set()

    def test_with_no_overrides_model_nothing_is_asked_for_them(self, alice, settings, django_assert_num_queries):
        settings.ISIK_FEATURES_OVERRIDES_MODEL = None

        with django_assert_num_queries(0):
            assert effective(alice, "theme") == Effective("light", "default")

    def test_only_grants_in_force(self, alice, fresh):
        now = timezone.now()
        grant(alice, "webhooks", source="manual", starts_at=now + timedelta(days=1))
        grant(alice, "beta", source="trial", starts_at=now - timedelta(days=2), expires_at=now - timedelta(days=1))
        grant(alice, "long_lived_tokens", source="manual")
        revoke(alice, "long_lived_tokens")

        assert resolution.held_features(fresh(alice)) == set()

    def test_in_sql_as_in_python(self, alice, bob, fresh):
        grant(alice, "long_lived_tokens", source="manual")
        set_setting(alice, "token_lifetime_days", 200)
        set_setting(bob, "token_lifetime_days", 120)
        force_setting(bob, "theme", "dark", source="staff", reason=REASON)
        carol = EmailUser.objects.create(username="carol", email="carol@example.com", first_name="Carol")
        # None of these unlocks anything for them now: another feature, one to come, one revoked.
        set_setting(carol, "token_lifetime_days", 150)
        grant(carol, "beta", source="manual")
        grant(bob, "long_lived_tokens", source="trial", starts_at=timezone.now() + timedelta(days=1))
        grant(carol, "long_lived_tokens", source="manual")
        revoke(carol, "long_lived_tokens")
        set_setting(alice, "session_length", timedelta(hours=2))
        set_setting(carol, "theme", "dark")

        dave = EmailUser.objects.create(username="dave", email="dave@example.com")
        for name in ("token_lifetime_days", "theme", "max_endpoints", "display_name", "session_length", "staff_note"):
            annotated = dict(
                EmailUser.objects.filter(pk__in=[alice.pk, bob.pk, carol.pk, dave.pk])
                .annotate(value=effective_expression(name))
                .values_list("pk", "value")
            )
            assert annotated == {user.pk: effective(fresh(user), name).value for user in (alice, bob, carol, dave)}, (
                name
            )

    def test_sql_clamps_from_below_too(self, alice, monkeypatch):
        setting = declarations.settings["token_lifetime_days"]
        monkeypatch.setitem(
            declarations.settings,
            "token_lifetime_days",
            Setting(**{**setting.__dict__, "without_feature": AtLeast(45)}),
        )
        set_setting(alice, "token_lifetime_days", 10)

        annotated = EmailUser.objects.filter(pk=alice.pk).annotate(value=effective_expression("token_lifetime_days"))
        assert annotated.get().value == 45


class TestWriting:
    def test_a_grant_names_a_declared_feature_and_a_source(self, alice):
        with pytest.raises(GrantRefused, match="^No feature called ghost is declared.$"):
            grant(alice, "ghost", source="manual")
        with pytest.raises(GrantRefused, match="^gift isn't one of GRANT_SOURCES.$"):
            grant(alice, "webhooks", source="gift")

    def test_a_grant_records_its_terms(self, alice):
        made = grant(alice, "webhooks", source="subscription", reference="inv-7")

        assert (made.feature_id, made.source, made.reference, made.revoked_at) == (
            "webhooks",
            "subscription",
            "inv-7",
            None,
        )

    def test_revoking_counts_what_it_revoked(self, alice):
        grant(alice, "webhooks", source="manual")
        grant(alice, "webhooks", source="trial")

        assert (revoke(alice, "webhooks"), revoke(alice, "webhooks")) == (2, 0)

    def test_a_setting_that_isnt_declared(self, alice):
        with pytest.raises(SettingRefused, match="^No setting called ghost is declared.$"):
            set_setting(alice, "ghost", 1)
        with pytest.raises(SettingRefused):
            release_setting(alice, "ghost")

    def test_a_value_the_setting_doesnt_take(self, alice):
        with pytest.raises(SettingInvalid, match="is not a valid choice"):
            set_setting(alice, "theme", "neon")
        with pytest.raises(SettingInvalid):
            force_setting(alice, "max_endpoints", 99, source="staff", reason=REASON)
        assert SettingInvalid.code == "setting_invalid"

    def test_a_forced_setting_isnt_the_holders_to_change(self, alice):
        force_setting(alice, "theme", "dark", source="staff", reason=REASON)

        with pytest.raises(SettingForced, match="^theme is forced, so it can't be changed.$"):
            set_setting(alice, "theme", "light")
        assert SettingForced.code == "setting_forced"

    def test_a_locked_setting_has_nothing_to_choose(self, alice):
        with pytest.raises(SettingLocked, match="^max_endpoints needs a feature you don't have.$"):
            set_setting(alice, "max_endpoints", 3)
        assert SettingLocked.code == "setting_locked"

    def test_forcing_needs_a_declared_source_and_a_reason(self, alice):
        with pytest.raises(ForceRefused):
            force_setting(alice, "theme", "dark", source="staff", reason="short")
        with pytest.raises(ForceRefused):
            force_setting(alice, "theme", "dark", source="nobody", reason=REASON)

    def test_a_source_staff_cant_lift(self, alice):
        force_setting(alice, "theme", "dark", source="account_review", reason=REASON)

        with pytest.raises(ForceRefused):
            release_setting(alice, "theme")
        assert release_setting(alice, "theme", by_operator=False) is True

    def test_the_overrides_row_is_made_on_the_first_write(self, alice):
        assert not UserOverrides.objects.exists()

        set_setting(alice, "theme", "dark")

        assert UserOverrides.objects.get().theme == "dark"


class TestReleasingEverythingASourceForced:
    def test_what_it_forced_and_nothing_else(self, alice, fresh, heard):
        set_setting(alice, "theme", "dark")
        force_setting(alice, "theme", "light", source="staff", reason=REASON)
        force_setting(alice, "session_length", timedelta(hours=1), source="staff", reason=REASON)
        force_setting(alice, "token_lifetime_days", 7, source="automated", reason="locked down")
        heard.clear()

        assert release_everything_forced_by(alice, source="staff") == 2

        assert effective(fresh(alice), "theme") == Effective("dark", "own")
        assert effective(alice, "session_length").origin == "default"
        assert effective(alice, "token_lifetime_days") == Effective(7, "forced", "automated", "locked down")
        assert heard == [
            ("setting", alice.pk, "theme", "light", "dark"),
            ("setting", alice.pk, "session_length", timedelta(hours=1), timedelta(hours=8)),
        ]

    def test_a_source_staff_cant_lift_lifts_itself(self, alice, fresh):
        force_setting(alice, "theme", "dark", source="account_review", reason=REASON)

        with pytest.raises(ForceRefused, match="can only be lifted by it"):
            release_everything_forced_by(alice, source="account_review")
        assert release_everything_forced_by(alice, source="account_review", by_operator=False) == 1
        assert effective(fresh(alice), "theme").origin == "default"

    def test_nothing_it_forced_changes_nothing(self, alice):
        assert release_everything_forced_by(alice, source="staff") == 0
        force_setting(alice, "theme", "dark", source="automated", reason="locked down")

        # No write, so the row's version - a holder's ETag - doesn't move.
        with CaptureQueriesContext(connection) as queries:
            assert release_everything_forced_by(alice, source="staff") == 0
        assert not [query for query in queries.captured_queries if query["sql"].startswith("UPDATE")]

    def test_a_source_that_isnt_declared(self, alice):
        with pytest.raises(ForceRefused, match="No force source"):
            release_everything_forced_by(alice, source="nobody")


class TestTelling:
    def test_a_grant_tells_its_feature_and_what_it_moved(self, alice, heard):
        set_setting(alice, "token_lifetime_days", 120)
        heard.clear()

        grant(alice, "long_lived_tokens", source="manual")
        grant(alice, "long_lived_tokens", source="trial")

        assert heard == [
            ("feature", alice.pk, "long_lived_tokens", True),
            ("setting", alice.pk, "token_lifetime_days", 90, 120),
        ]

    def test_a_revoke_tells_what_it_moved(self, alice, heard):
        grant(alice, "webhooks", source="manual")
        heard.clear()

        revoke(alice, "webhooks")
        revoke(alice, "webhooks")

        assert heard == [("feature", alice.pk, "webhooks", False), ("setting", alice.pk, "max_endpoints", 5, 0)]

    def test_a_grant_to_come_tells_nothing_yet(self, alice, heard):
        grant(alice, "webhooks", source="manual", starts_at=timezone.now() + timedelta(days=1))

        assert heard == []

    def test_a_force_and_a_write_tell_what_they_moved(self, alice, heard):
        force_setting(alice, "theme", "dark", source="staff", reason=REASON)
        force_setting(alice, "theme", "dark", source="automated", reason="again")
        set_setting(alice, "session_length", timedelta(hours=1))

        assert heard == [
            ("setting", alice.pk, "theme", "light", "dark"),
            ("setting", alice.pk, "session_length", timedelta(hours=8), timedelta(hours=1)),
        ]


class TestTheSweep:
    def test_the_first_sweep_starts_the_clock(self):
        assert sweep_boundaries() == 0
        assert SweepCursor.objects.count() == 1

    def test_a_grant_starting_and_ending_later_is_told_each_as_of_its_moment(self, alice, heard):
        # Both between two sweeps, and the sweep run long after: each told as it was then, in order.
        now = timezone.now()
        sweep_boundaries(now)
        set_setting(alice, "token_lifetime_days", 120)
        grant(
            alice,
            "long_lived_tokens",
            source="trial",
            starts_at=now + timedelta(hours=1),
            expires_at=now + timedelta(hours=2),
        )
        heard.clear()

        assert sweep_boundaries(now + timedelta(days=1)) == 2

        assert heard == [
            ("feature", alice.pk, "long_lived_tokens", True),
            ("setting", alice.pk, "token_lifetime_days", 90, 120),
            ("feature", alice.pk, "long_lived_tokens", False),
            ("setting", alice.pk, "token_lifetime_days", 120, 90),
        ]
        assert SweepCursor.objects.get().swept_at == now + timedelta(days=1)

    def test_a_start_told_when_it_was_granted_isnt_told_again(self, alice, heard):
        now = timezone.now()
        sweep_boundaries(now - timedelta(hours=1))
        grant(alice, "webhooks", source="manual")
        grant(alice, "beta", source="manual", starts_at=now - timedelta(minutes=30))
        heard.clear()

        assert sweep_boundaries(now + timedelta(minutes=1)) == 0
        assert heard == []

    def test_an_end_is_the_sweeps_even_for_a_grant_started_when_written(self, alice, heard):
        now = timezone.now()
        sweep_boundaries(now)
        grant(alice, "long_lived_tokens", source="trial", expires_at=now + timedelta(hours=1))
        heard.clear()

        assert sweep_boundaries(now + timedelta(hours=2)) == 1
        assert heard == [("feature", alice.pk, "long_lived_tokens", False)]

    def test_a_boundary_another_grant_covers_tells_nothing(self, alice, heard):
        now = timezone.now()
        sweep_boundaries(now)
        grant(alice, "beta", source="manual")
        grant(alice, "beta", source="trial", starts_at=now + timedelta(hours=1), expires_at=now + timedelta(hours=2))
        heard.clear()

        assert sweep_boundaries(now + timedelta(hours=3)) == 2
        assert heard == []

    def test_a_setting_the_boundary_doesnt_move_isnt_told(self, alice, heard):
        # Their own 30 is under the 90 ceiling either way: the feature moves, the setting doesn't.
        now = timezone.now()
        sweep_boundaries(now)
        grant(alice, "long_lived_tokens", source="trial", starts_at=now + timedelta(hours=1))
        heard.clear()

        assert sweep_boundaries(now + timedelta(hours=2)) == 1
        assert heard == [("feature", alice.pk, "long_lived_tokens", True)]

    def test_a_revoked_grants_later_boundaries_arent_told(self, alice, heard):
        now = timezone.now()
        sweep_boundaries(now)
        grant(alice, "beta", source="trial", starts_at=now + timedelta(hours=1))
        grant(alice, "webhooks", source="trial", expires_at=now + timedelta(hours=1))
        revoke(alice, "beta")
        revoke(alice, "webhooks")
        heard.clear()

        assert sweep_boundaries(now + timedelta(hours=2)) == 0
        assert heard == []

    def test_a_grant_over_before_it_was_written_tells_nothing(self, alice, heard):
        now = timezone.now()
        sweep_boundaries(now - timedelta(days=10))
        heard.clear()
        grant(alice, "beta", source="manual", starts_at=now - timedelta(days=9), expires_at=now - timedelta(days=8))

        assert sweep_boundaries(now + timedelta(minutes=1)) == 0
        assert heard == []


class TestTheGrantLedger:
    def test_a_grant_is_never_deleted(self, alice):
        grant(alice, "webhooks", source="manual")

        with transaction.atomic(), pytest.raises(ProgrammingError, match="Cannot delete"):
            FeatureGrant.objects.all().delete()

    def test_nor_truncated(self):
        # On an empty table: a grant written in this transaction leaves deferred key checks a TRUNCATE won't pass.
        with pytest.raises(ProgrammingError, match="Cannot truncate"), connection.cursor() as cursor:
            cursor.execute(f"TRUNCATE {FeatureGrant._meta.db_table}")

    def test_its_terms_never_change_and_its_revoke_is_written_once(self, alice):
        made = grant(alice, "webhooks", source="manual")

        with transaction.atomic(), pytest.raises(ProgrammingError, match="Cannot update"):
            FeatureGrant.objects.filter(pk=made.pk).update(source="trial")
        FeatureGrant.objects.filter(pk=made.pk).update(revoked_at=timezone.now())
        with transaction.atomic(), pytest.raises(ProgrammingError, match="Cannot update"):
            FeatureGrant.objects.filter(pk=made.pk).update(revoked_at=timezone.now())

    def test_a_feature_row_outlives_its_declaration(self):
        assert set(Feature.objects.values_list("name", flat=True)) >= set(declarations.features)
        with transaction.atomic(), pytest.raises(ProgrammingError, match="Cannot delete"):
            Feature.objects.filter(pk="beta").delete()

    def test_a_force_is_whole_or_absent(self, alice):
        with transaction.atomic(), pytest.raises(IntegrityError, match="theme_forced_exactly"):
            UserOverrides.objects.create(holder=alice, theme_forced=True, theme_forced_value="dark")
        with transaction.atomic(), pytest.raises(IntegrityError, match="max_endpoints_forced_exactly"):
            UserOverrides.objects.create(
                holder=alice,
                max_endpoints_forced=True,
                max_endpoints_forced_source="staff",
                max_endpoints_forced_reason="x",
            )


def with_grants_loaded(holder):
    """`holder` as `prefetch_related` of its grants leaves it - beside another relation loaded first."""
    grants = FeatureGrant.objects.filter(holder=holder)
    groups = holder.groups.all()
    list(grants), list(groups)
    holder._prefetched_objects_cache = {"groups": groups, "grants": grants}
    return holder


class TestErasing:
    def test_a_holders_grants_overrides_and_their_history_go(self, alice, bob):
        grant(alice, "webhooks", source="manual")
        force_setting(alice, "theme", "dark", source="staff", reason=REASON)
        grant(bob, "webhooks", source="manual")

        erase_holder(alice)

        assert list(FeatureGrant.objects.values_list("holder", flat=True)) == [bob.pk]
        assert not UserOverrides.objects.filter(holder=alice).exists()
        assert not UserOverrides.pgh_event_model.objects.filter(holder_id=alice.pk).exists()

    def test_a_project_keeping_no_settings(self, alice, settings):
        grant(alice, "webhooks", source="manual")
        settings.ISIK_FEATURES_OVERRIDES_MODEL = None

        erase_holder(alice)

        assert not FeatureGrant.objects.exists()

    def test_deleting_the_holder_erases_them_with_it(self, alice, bob):
        grant(alice, "webhooks", source="manual")
        force_setting(alice, "theme", "dark", source="staff", reason=REASON)
        grant(bob, "webhooks", source="manual")

        alice.delete()

        assert list(FeatureGrant.objects.values_list("holder", flat=True)) == [bob.pk]
        assert not UserOverrides.objects.exists()
        assert not UserOverrides.pgh_event_model.objects.filter(holder_id=alice.pk).exists()

    def test_a_delete_that_fails_erases_nothing(self, alice):
        grant(alice, "webhooks", source="manual")
        held_by = alice.pk

        with pytest.raises(RuntimeError), transaction.atomic():
            alice.delete()
            raise RuntimeError("What the delete was part of failed.")

        assert FeatureGrant.objects.filter(holder=held_by).exists()

    def test_a_key_declared_protect_refuses_the_holders_delete(self, alice, monkeypatch):
        grant(alice, "webhooks", source="manual")
        force_setting(alice, "theme", "dark", source="staff", reason=REASON)
        monkeypatch.setattr(FeatureGrant._meta.get_field("holder").remote_field, "on_delete", models.PROTECT)

        with pytest.raises(ProtectedError), transaction.atomic():
            alice.delete()

        assert UserOverrides.objects.filter(holder=alice).exists()
        erase_holder(alice)
        alice.delete()
        assert not FeatureGrant.objects.exists()

    def test_only_the_keys_declared_erase_are_erased(self, alice, monkeypatch):
        grant(alice, "webhooks", source="manual")
        force_setting(alice, "theme", "dark", source="staff", reason=REASON)
        monkeypatch.setattr(FeatureGrant._meta.get_field("holder").remote_field, "on_delete", models.DO_NOTHING)

        with pytest.raises(IntegrityError), transaction.atomic():
            alice.delete()
            assert (UserOverrides.objects.exists(), FeatureGrant.objects.exists()) == (False, True)
            connection.check_constraints()


class TestGuards:
    def test_a_guard_needs_its_feature(self, alice, fresh):
        guard = requires_feature("webhooks")

        with pytest.raises(FeatureMissing, match="^This needs the feature webhooks.$"):
            guard(alice)
        grant(alice, "webhooks", source="manual")
        guard(fresh(alice))
        assert "webhooks" in declarations.checked
        assert FeatureMissing.code == "feature_missing"


def test_a_holder_without_overrides_reads_its_defaults(alice, settings, fresh):
    settings.ISIK_FEATURES_OVERRIDES_MODEL = None

    assert effective(fresh(alice), "theme") == Effective("light", "default")
    assert resolution.overrides_of(alice) is None


def test_grants_and_overrides_are_read_where_they_live(alice, settings, fresh):
    entered = []

    class Recorded:
        def __enter__(self):
            entered.append("in")

        def __exit__(self, *exc):
            return False

    settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "IN_HOLDER_SCHEMA": lambda: Recorded()}

    effective(fresh(alice), "theme")

    assert entered == ["in"]


def test_the_command_sweeps(capsys):
    from django.core.management import call_command

    call_command("features", "--sweep")
    call_command("features")

    assert capsys.readouterr().out == "Told 0 boundaries.\n"


def test_the_task_sweeps():
    assert tasks.sweep_boundaries() == 0
    assert operations.sweep_boundaries() == 0


class TestTheBeatSchedule:
    def test_its_entry(self):
        assert schedule.beat_schedule() == {
            "isik.features.sweep": {"task": tasks.sweep_boundaries.name, "schedule": timedelta(minutes=1)}
        }
        assert schedule.beat_schedule(sweep_every=5)["isik.features.sweep"]["schedule"] == 5

    def test_its_merged_once_the_app_is_configured(self):
        app = Celery("merging", set_as_current=False)
        app.conf.beat_schedule = {"renew": {"task": "app.renew", "schedule": 60}}

        schedule.merge_beat_schedule(app, sweep_every=5)
        assert app.configured is False
        gc.collect()

        assert app.conf.beat_schedule == {
            "renew": {"task": "app.renew", "schedule": 60},
            **schedule.beat_schedule(sweep_every=5),
        }

    def test_into_an_app_with_no_schedule_of_its_own(self):
        app = Celery("merging", set_as_current=False)

        schedule.merge_beat_schedule(app)

        assert app.conf.beat_schedule == schedule.beat_schedule()


class TestAFixedValueWithoutTheFeature:
    def test_its_own_value_without_the_feature_and_the_default_with_it(self, alice, bob, fresh):
        assert effective(alice, "max_endpoints") == Effective(0, "feature_missing")

        grant(alice, "webhooks", source="manual")

        assert effective(fresh(alice), "max_endpoints") == Effective(5, "default")
        assert dict(
            EmailUser.objects.filter(pk__in=[alice.pk, bob.pk])
            .annotate(endpoints=effective_expression("max_endpoints"))
            .values_list("username", "endpoints")
        ) == {"alice": 5, "bob": 0}

    def test_staff_set_what_the_holder_may_only_read(self, alice, fresh):
        grant(alice, "webhooks", source="manual")

        set_setting(alice, "max_endpoints", 20)

        assert effective(fresh(alice), "max_endpoints") == Effective(20, "own")
        assert declarations.settings["max_endpoints"].holder_may_change is False


class TestASettingReadOnTheHolder:
    def test_it_reads_what_is_in_force_and_keeps_their_own(self, alice, fresh):
        assert alice.display_name == "Alice"

        force_setting(alice, "display_name", "Account under review", source="staff", reason=REASON)

        assert fresh(alice).display_name == "Account under review"
        alice.save()
        alice.refresh_from_db()
        assert (alice.first_name, alice.display_name) == ("Alice", "Account under review")

        release_setting(alice, "display_name")

        assert fresh(alice).display_name == "Alice"

    def test_setting_it_sets_their_own(self, alice):
        alice.display_name = "Al"
        alice.save()

        assert EmailUser.objects.get(pk=alice.pk).first_name == "Al"

    def test_on_the_class_it_is_the_resolver(self):
        assert isinstance(EmailUser.display_name, resolution.ResolvedSetting)
        assert EmailUser.display_name.name == "display_name"

    def test_never_at_its_own_fields_name(self, monkeypatch):
        monkeypatch.setattr(declarations, "settings", dict(declarations.settings))

        with pytest.raises(
            ImproperlyConfigured,
            match=(
                "^nick resolves as nick, its own value's field - a save would keep a forced value as the holder's "
                "own. Keep the own value in a field of another name.$"
            ),
        ):
            declarations.declare_setting(
                "nick", models.CharField(max_length=9), "", own=declarations.OnHolder("nick", resolved_as="nick")
            )

    def test_nor_at_another_field_of_the_holder(self, monkeypatch):
        clashing = Setting("nick", models.CharField(max_length=9), "", own=declarations.OnHolder("first_name", "email"))
        monkeypatch.setattr(declarations, "settings", {"nick": clashing})

        with pytest.raises(
            ImproperlyConfigured,
            match="^nick resolves as testapp.EmailUser.email, which is a field of its - name another attribute.$",
        ):
            resolution.resolve_on_holder()

    @pytest.mark.parametrize("taken", ["manager_id", "groups"], ids=["a key's column", "a relation"])
    def test_nor_at_any_name_the_holder_answers_for(self, monkeypatch, taken):
        clashing = Setting("nick", models.CharField(max_length=9), "", own=declarations.OnHolder("first_name", taken))
        monkeypatch.setattr(declarations, "settings", {"nick": clashing})

        with pytest.raises(ImproperlyConfigured, match=f"^nick resolves as testapp.EmailUser.{taken}, "):
            resolution.resolve_on_holder()

    def test_each_one_resolved_on_the_holder_is_put_there(self, monkeypatch, alice):
        plain = Setting("plain", models.CharField(max_length=9), "", own=declarations.OnHolder("last_name"))
        nick = Setting("nick", models.CharField(max_length=9), "x", own=declarations.OnHolder("first_name", "nick"))
        monkeypatch.setattr(declarations, "settings", {"plain": plain, "nick": nick})
        monkeypatch.delattr(EmailUser, "nick", raising=False)

        resolution.resolve_on_holder()

        try:
            assert (EmailUser.nick.name, alice.nick) == ("nick", "Alice")
        finally:
            del EmailUser.nick

    def test_a_setting_not_resolved_on_the_holder_puts_nothing_there(self, monkeypatch):
        plain = Setting("nick", models.CharField(max_length=9), "", own=declarations.OnHolder("first_name"))
        monkeypatch.setattr(declarations, "settings", {"nick": plain})

        resolution.resolve_on_holder()

        assert not hasattr(EmailUser, "nick")


class TestWhatAGrantOrARevokeTouches:
    def test_a_grant_another_holder_has_tells_this_one(self, alice, bob, heard):
        grant(bob, "webhooks", source="manual")
        grant(alice, "beta", source="manual")
        heard.clear()

        grant(alice, "webhooks", source="manual")

        assert heard[0] == ("feature", alice.pk, "webhooks", True)

    def test_a_revoke_touches_only_the_holders_own(self, alice, bob, fresh):
        grant(alice, "webhooks", source="manual")
        grant(bob, "webhooks", source="manual")

        assert revoke(alice, "webhooks") == 1
        assert has_feature(fresh(bob), "webhooks")

    def test_a_boundary_before_the_last_sweep_isnt_told_again(self, alice, heard):
        now = timezone.now()
        sweep_boundaries(now)
        grant(alice, "beta", source="trial", starts_at=now + timedelta(hours=1))
        sweep_boundaries(now + timedelta(hours=2))
        heard.clear()

        assert sweep_boundaries(now + timedelta(hours=3)) == 0
        assert heard == []

    def test_boundaries_at_one_moment_are_each_told(self, alice, heard):
        now = timezone.now()
        sweep_boundaries(now)
        at = now + timedelta(hours=1)
        grant(alice, "beta", source="trial", starts_at=now + timedelta(minutes=30), expires_at=at)
        grant(alice, "webhooks", source="trial", starts_at=at)
        heard.clear()

        assert sweep_boundaries(now + timedelta(hours=2)) == 3
        assert {event for event in heard if event[0] == "feature"} == {
            ("feature", alice.pk, "beta", True),
            ("feature", alice.pk, "beta", False),
            ("feature", alice.pk, "webhooks", True),
        }

    def test_a_default_without_the_feature(self, alice):
        assert effective(alice, "staff_note") == Effective("", "feature_missing")

    def test_the_holder_model_a_project_named_is_kept(self, settings):
        settings.ISIK_FEATURES_HOLDER_MODEL = "testapp.Tag"

        swapping.set_defaults()

        assert settings.ISIK_FEATURES_HOLDER_MODEL == "testapp.Tag"
