"""The features app's REST views - features, settings and their PATCH - through the real handler."""

from datetime import timedelta

import pytest
from django.db import IntegrityError, connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.drainage import GENERATOR_STATS
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APIClient

from isik.django.apps.features.drf import defaults_etag
from isik.django.apps.features.operations import force_setting, grant, revoke, set_setting
from isik.django.apps.features.resolution import forget
from tests.django.apps.features.api import urls
from tests.testapp.models import UserOverrides


pytestmark = pytest.mark.django_db

REASON = "the account is under investigation and stays short-lived until it's done"


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def settings_of(client):
    answered = client.get("/settings/")
    return {each["name"]: each for each in answered.json()}, answered["ETag"]


def patch(client, name, kind, value, etag="", **headers):
    if etag is not None:
        headers["HTTP_IF_MATCH"] = etag
    return client.patch(f"/settings/{name}/", {"type": kind, "value": value}, format="json", **headers)


class TestFeatures:
    def test_the_holders_features_with_their_grants(self, alice):
        now = timezone.now()
        grant(alice, "webhooks", source="subscription", reference="inv-7")
        grant(alice, "beta", source="trial", starts_at=now + timedelta(days=1), expires_at=now + timedelta(days=8))
        grant(
            alice,
            "long_lived_tokens",
            source="trial",
            starts_at=now - timedelta(days=9),
            expires_at=now - timedelta(days=1),
        )
        forget(alice)

        listed = {each["name"]: each for each in client_for(alice).get("/features/").json()}

        assert sorted(listed) == ["beta", "long_lived_tokens", "webhooks"]
        assert listed["webhooks"]["held"] is True
        assert listed["webhooks"]["grants"][0]["source"] is None
        assert (listed["beta"]["held"], len(listed["beta"]["grants"])) == (False, 1)
        assert listed["long_lived_tokens"] == {
            "name": "long_lived_tokens",
            "label": "Long-lived tokens",
            "description": None,
            "held": False,
            "grants": [],
        }

    def test_a_grants_source_shown_where_the_project_shows_it(self, alice, settings):
        settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "SHOW_GRANT_SOURCE": True}
        grant(alice, "webhooks", source="subscription", reference="inv-7")

        shown = {each["name"]: each for each in client_for(alice).get("/features/").json()}["webhooks"]["grants"][0]

        assert (shown["source"], shown["reference"]) == ("subscription", "inv-7")

    def test_the_features_a_project_lets_a_holder_see(self, alice, settings):
        settings.ISIK_FEATURES = {
            **settings.ISIK_FEATURES,
            "FEATURES_VISIBLE": lambda request, holder: ["internal_flag"],
        }

        assert [each["name"] for each in client_for(alice).get("/features/").json()] == ["internal_flag"]

    def test_a_view_gated_on_a_feature(self, alice):
        assert client_for(alice).get("/webhooks/").status_code == 403
        assert APIClient().get("/webhooks/").status_code in (401, 403)

        grant(alice, "webhooks", source="manual")

        assert client_for(alice).get("/webhooks/").json() == {"ok": True}


class TestListingSettings:
    def test_each_setting_typed_with_its_value_and_why(self, alice):
        listed, etag = settings_of(client_for(alice))

        assert etag == defaults_etag()
        assert sorted(listed) == [
            "display_name",
            "max_endpoints",
            "session_length",
            "theme",
            "token_lifetime_days",
            "two_factor_required",
        ]
        assert listed["max_endpoints"] == {
            "name": "max_endpoints",
            "type": "int",
            "label": "Webhook endpoints",
            "description": "How many endpoints you may register.",
            "value": 0,
            "default": 5,
            "origin": "feature_missing",
            "forced_reason": None,
            "may_change": False,
            "chosen": None,
            "requires_feature": "webhooks",
            "minimum": 0,
            "maximum": 50,
        }
        assert listed["theme"]["choices"] == [{"value": "light", "label": "Light"}, {"value": "dark", "label": "Dark"}]
        assert (listed["display_name"]["value"], listed["display_name"]["origin"]) == ("Alice", "own")
        assert listed["session_length"]["value"] == "08:00:00"

    def test_a_forced_setting_says_why_where_its_source_lets_it(self, alice):
        force_setting(alice, "theme", "dark", source="staff", reason=REASON)
        force_setting(alice, "session_length", timedelta(hours=1), source="account_review", reason=REASON)

        listed, etag = settings_of(client_for(alice))

        assert (listed["theme"]["origin"], listed["theme"]["forced_reason"], listed["theme"]["may_change"]) == (
            "forced",
            REASON,
            False,
        )
        assert listed["session_length"]["forced_reason"] is None
        assert etag != defaults_etag()

    def test_their_own_choice_beside_what_is_in_force(self, alice):
        set_setting(alice, "theme", "light")
        force_setting(alice, "theme", "dark", source="staff", reason=REASON)

        listed, _etag = settings_of(client_for(alice))

        assert (listed["theme"]["value"], listed["theme"]["chosen"], listed["theme"]["requires_feature"]) == (
            "dark",
            "light",
            None,
        )

    def test_the_feature_it_needs_only_where_the_holder_may_see_it(self, alice, settings):
        settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "FEATURES_VISIBLE": lambda request, holder: []}

        listed, _etag = settings_of(client_for(alice))

        assert listed["max_endpoints"]["requires_feature"] is None

    def test_the_settings_a_project_lets_a_holder_see_and_change(self, alice, settings):
        settings.ISIK_FEATURES = {
            **settings.ISIK_FEATURES,
            "SETTINGS_VISIBLE": lambda request, holder: ["theme", "staff_note"],
            "MAY_CHANGE_SETTING": lambda request, holder, name: False,
        }

        listed, _etag = settings_of(client_for(alice))

        assert sorted(listed) == ["staff_note", "theme"]
        assert listed["theme"]["may_change"] is False


class TestChangingASetting:
    def test_the_first_write_names_the_defaults_and_makes_the_row(self, alice):
        client = client_for(alice)

        answered = patch(client, "theme", "choice", "dark", defaults_etag())

        assert answered.status_code == 200
        assert (answered.json()["value"], answered.json()["origin"]) == ("dark", "own")
        assert answered["ETag"] != defaults_etag()
        assert UserOverrides.objects.get(holder=alice).theme == "dark"

    def test_the_next_names_the_rows_version(self, alice):
        client = client_for(alice)
        first = patch(client, "theme", "choice", "dark", defaults_etag())

        stale = patch(client, "session_length", "duration", "01:00:00", defaults_etag())
        current = patch(client, "session_length", "duration", "01:00:00", first["ETag"])

        assert (stale.status_code, stale.json()["code"]) == (412, "precondition_failed")
        assert current.status_code == 200

    def test_any_version_with_a_star(self, alice):
        assert patch(client_for(alice), "theme", "choice", "dark", "*").status_code == 200

    def test_without_if_match(self, alice):
        answered = patch(client_for(alice), "theme", "choice", "dark", None)

        assert (answered.status_code, answered.json()["code"]) == (428, "precondition_required")

    def test_two_first_writes_at_once_dont_both_apply(self, alice, monkeypatch):
        def made_meanwhile(*args, **kwargs):
            raise IntegrityError("duplicate key value violates unique constraint")

        monkeypatch.setattr(UserOverrides.objects, "create", made_meanwhile)

        assert patch(client_for(alice), "theme", "choice", "dark", defaults_etag()).status_code == 412

    def test_a_setting_that_isnt_shown(self, alice):
        assert patch(client_for(alice), "staff_note", "text", "x", defaults_etag()).status_code == 404

    def test_the_type_it_holds(self, alice):
        answered = patch(client_for(alice), "theme", "int", 1, defaults_etag())

        assert (answered.status_code, answered.json()) == (400, {"type": ["theme holds a choice."]})

    def test_a_value_it_doesnt_take(self, alice):
        answered = patch(client_for(alice), "theme", "choice", "neon", defaults_etag())

        assert answered.status_code == 400
        assert "is not a valid choice" in answered.json()["value"][0]

    def test_a_forced_or_locked_one_isnt_the_holders(self, alice):
        force_setting(alice, "theme", "dark", source="staff", reason=REASON)
        client = client_for(alice)
        _listed, etag = settings_of(client)

        assert patch(client, "theme", "choice", "light", etag).status_code == 403
        assert patch(client, "max_endpoints", "int", 3, etag).status_code == 403

    def test_a_refusal_the_write_meets_carries_its_code(self, alice, monkeypatch):
        from isik.django.apps.features import drf

        monkeypatch.setattr(drf.SettingViewSet, "may_change", lambda self, holder, name, origin: True)
        force_setting(alice, "theme", "dark", source="staff", reason=REASON)
        client = client_for(alice)
        _listed, etag = settings_of(client)

        forced = patch(client, "theme", "choice", "light", etag)
        locked = patch(client, "max_endpoints", "int", 3, etag)

        assert (forced.status_code, forced.json()["code"]) == (409, "setting_forced")
        assert (locked.status_code, locked.json()["code"]) == (409, "setting_locked")

    def test_a_setting_behind_a_request_policy(self, alice):
        client = client_for(alice)

        refused = patch(client, "two_factor_required", "bool", True, defaults_etag())
        allowed = patch(
            client, "two_factor_required", "bool", True, defaults_etag(), HTTP_X_PROOF="fresh for two_factor_required"
        )

        assert (refused.status_code, refused.json()["code"]) == (403, "reauthentication_required")
        assert refused["X-Reauthentication-Required"] == "true"
        assert allowed.status_code == 200


class TestTheSchema:
    def schema(self):
        with override_settings(REST_FRAMEWORK={"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}):
            return SchemaGenerator(patterns=urls.urlpatterns).get_schema(request=None, public=True)

    def test_it_is_generated_with_no_warning(self):
        GENERATOR_STATS.reset()

        self.schema()

        assert (dict(GENERATOR_STATS._warn_cache), dict(GENERATOR_STATS._error_cache)) == ({}, {})

    def test_a_setting_is_a_union_on_its_type(self):
        generated = self.schema()
        setting = generated["components"]["schemas"]["Setting"]

        assert setting["discriminator"]["propertyName"] == "type"
        assert set(setting["discriminator"]["mapping"]) == {
            "bool",
            "int",
            "decimal",
            "float",
            "duration",
            "text",
            "choice",
        }
        body = generated["paths"]["/settings/{name}/"]["patch"]["requestBody"]["content"]["application/json"]["schema"]
        assert body == {"$ref": "#/components/schemas/PatchedSettingValue"}


class TestWhatTheSchemaTellsAClient:
    def operation(self, path, method):
        return TestTheSchema().schema()["paths"][path][method]

    def test_a_change_names_the_version_it_read_and_every_answer(self):
        patched = self.operation("/settings/{name}/", "patch")

        headers = {each["name"]: each for each in patched["parameters"] if each["in"] == "header"}
        assert headers["If-Match"]["required"] is True
        assert sorted(patched["responses"]) == ["200", "400", "403", "404", "409", "412", "428"]
        assert "ETag" in patched["responses"]["200"]["headers"]

    def test_the_listing_sends_the_version(self):
        assert "ETag" in self.operation("/settings/", "get")["responses"]["200"]["headers"]


class TestWhatAFeaturesListingShows:
    def test_only_the_holders_grants_in_force_or_to_come(self, alice, bob):
        later = timezone.now() + timedelta(days=3)
        grant(alice, "beta", source="trial", expires_at=later)
        grant(bob, "webhooks", source="manual")
        grant(alice, "long_lived_tokens", source="manual")
        revoke(alice, "long_lived_tokens")

        listed = {each["name"]: each for each in client_for(alice).get("/features/").json()}

        assert listed["beta"] == {
            "name": "beta",
            "label": "Beta",
            "description": "Try what's coming.",
            "held": True,
            "grants": [
                {
                    "starts_at": listed["beta"]["grants"][0]["starts_at"],
                    "expires_at": listed["beta"]["grants"][0]["expires_at"],
                    "source": None,
                    "reference": None,
                }
            ],
        }
        assert parse_datetime(listed["beta"]["grants"][0]["expires_at"]) == later
        assert (listed["webhooks"]["grants"], listed["long_lived_tokens"]["grants"]) == ([], [])


class TestTheProjectsHooks:
    def test_the_holder_a_request_is_about(self, alice, bob, settings):
        asked = []

        def holder(request):
            asked.append(request.user)
            return bob

        settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "HOLDER": holder}
        grant(bob, "webhooks", source="manual")

        listed = {each["name"]: each["held"] for each in client_for(alice).get("/features/").json()}

        assert listed["webhooks"] is True
        assert client_for(alice).get("/webhooks/").json() == {"ok": True}
        assert set(asked) == {alice}

    def test_what_each_hook_is_asked_with(self, alice, settings):
        asked = []

        def visible(request, holder):
            asked.append(("visible", request.user, holder))
            return ["theme", "beta"]

        def may_change(request, holder, name):
            asked.append(("may_change", request.user, holder, name))
            return True

        settings.ISIK_FEATURES = {
            **settings.ISIK_FEATURES,
            "FEATURES_VISIBLE": visible,
            "SETTINGS_VISIBLE": visible,
            "MAY_CHANGE_SETTING": may_change,
        }
        client = client_for(alice)

        client.get("/features/")
        client.get("/settings/")
        patch(client, "theme", "choice", "dark", defaults_etag())

        assert set(asked) == {("visible", alice, alice), ("may_change", alice, alice, "theme")}

    def test_by_default_the_holder_may_change_their_own(self, alice):
        listed, _etag = settings_of(client_for(alice))

        assert listed["theme"]["may_change"] is True

    def test_one_the_holder_may_only_read(self, alice):
        grant(alice, "webhooks", source="manual")
        client = client_for(alice)
        listed, etag = settings_of(client)

        assert (listed["max_endpoints"]["value"], listed["max_endpoints"]["may_change"]) == (5, False)
        assert patch(client, "max_endpoints", "int", 9, etag).status_code == 403

    def test_no_overrides_model_reads_the_defaults_version(self, alice, settings):
        settings.ISIK_FEATURES_OVERRIDES_MODEL = None

        assert settings_of(client_for(alice))[1] == defaults_etag()


class TestTheHeadersAndBodies:
    def test_the_version_is_sent_as_etag(self, alice):
        client = client_for(alice)

        listed = client.get("/settings/")
        changed = patch(client, "theme", "choice", "dark", defaults_etag())

        assert ("ETag", defaults_etag()) in listed.items()
        assert ("ETag", changed["ETag"]) in changed.items()

    def test_a_weak_tag_never_matches(self, alice):
        answered = patch(client_for(alice), "theme", "choice", "dark", f"W/{defaults_etag()}")

        assert answered.status_code == 412

    def test_a_weak_tag_matches_where_the_project_says(self, alice, settings):
        settings.PRECONDITION_IGNORES_WEAK_TAGS = True

        assert patch(client_for(alice), "theme", "choice", "dark", f"W/{defaults_etag()}").status_code == 200

    def test_a_refusal_says_why(self, alice, monkeypatch):
        from isik.django.apps.features import drf

        monkeypatch.setattr(drf.SettingViewSet, "may_change", lambda self, holder, name, origin: True)
        force_setting(alice, "theme", "dark", source="staff", reason=REASON)
        client = client_for(alice)

        answered = patch(client, "theme", "choice", "light", settings_of(client)[1])

        assert answered.json() == {
            "detail": "theme is forced, so it can't be changed.",
            "code": "setting_forced",
        }

    def test_a_policys_refusal_says_why(self, alice):
        refused = patch(
            client_for(alice), "two_factor_required", "bool", True, defaults_etag(), HTTP_X_PROOF="fresh for theme"
        )

        assert refused.json() == {"detail": "Prove who you are first.", "code": "reauthentication_required"}


def test_the_features_list_reads_each_grants_feature_with_it(alice):
    grant(alice, "webhooks", source="manual")
    grant(alice, "beta", source="manual")

    with CaptureQueriesContext(connection) as queries:
        client_for(alice).get("/features/")

    # The grants' query selects their features' columns - each grant's name read with it, not after.
    assert [
        query["sql"]
        for query in queries
        if 'FROM "isik_feature_grant"' in query["sql"] and '"isik_feature"."created_at"' in query["sql"]
    ] != []
