"""PreconditionMixin's five options - each a setting, overridden per view."""

import uuid

import pytest
from django.http import Http404
from django.test import override_settings
from drf_spectacular.drainage import GENERATOR_STATS, reset_generator_stats
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status

from isik.django.apps.common.checks import every_precondition_view_has_options_that_make_sense
from isik.django.apps.common.db.concurrency import current_concurrency_etag
from isik.django.drf.coverage import CoverageStatus, precondition_coverage
from isik.django.drf.viewsets import PreconditionMixin
from isik.django.drf.viewsets.preconditions import (
    PRECONDITION_DEFAULTS,
    PreconditionFailed,
    entity_tags,
    precondition_option_problems,
    precondition_option_warnings,
)
from tests.django.drf.preconditions import option_urls, views
from tests.testapp.models import Widget


OPTIONS = "tests.django.drf.preconditions.option_urls"
FINE = "tests.django.drf.preconditions.urls"


@pytest.fixture
def option_urls_served(settings):
    settings.ROOT_URLCONF = OPTIONS


@pytest.fixture
def widget(alice):
    return Widget.objects.create(name="bolt", owner=alice)


def etag(widget):
    return current_concurrency_etag(Widget, widget.pk)


def patch(client, path, if_match=None):
    headers = {} if if_match is None else {"If-Match": if_match}
    return client.patch(path, {"name": "nut"}, format="json", headers=headers)


class TestReadingAnOption:
    def test_the_defaults_are_the_agreed_ones(self):
        assert PRECONDITION_DEFAULTS == {
            "ignores_weak_tags": True,
            "required": True,
            "allows_any": True,
            "missing_row_status": 404,
            "not_modified": True,
        }

    @pytest.mark.parametrize("name", list(PRECONDITION_DEFAULTS))
    def test_without_a_setting_or_an_attribute_its_the_default(self, name):
        assert PreconditionMixin.precondition_option(name) == PRECONDITION_DEFAULTS[name]

    @pytest.mark.parametrize("name", list(PRECONDITION_DEFAULTS))
    def test_a_setting_overrides_the_default(self, settings, name):
        setattr(settings, f"PRECONDITION_{name.upper()}", "from settings")

        assert PreconditionMixin.precondition_option(name) == "from settings"

    @pytest.mark.parametrize("name", list(PRECONDITION_DEFAULTS))
    def test_a_views_attribute_overrides_the_setting(self, settings, name):
        setattr(settings, f"PRECONDITION_{name.upper()}", "from settings")
        view = type("Own", (PreconditionMixin,), {f"precondition_{name}": False})

        assert view.precondition_option(name) is False


@pytest.mark.django_db
@pytest.mark.usefixtures("option_urls_served", "atomic_requests")
class TestWeakTags:
    def test_a_strict_view_refuses_a_weak_tag(self, client, widget):
        response = patch(client, f"/strict/{widget.pk}/", f"W/{etag(widget)}")

        assert response.status_code == status.HTTP_412_PRECONDITION_FAILED

    def test_a_strict_view_takes_the_strong_one(self, client, widget):
        assert patch(client, f"/strict/{widget.pk}/", etag(widget)).status_code == status.HTTP_200_OK

    def test_its_if_none_match_stays_weak_as_rfc_9110_has_it(self, client, widget):
        response = client.get(f"/strict/{widget.pk}/", headers={"If-None-Match": f"W/{etag(widget)}"})

        assert response.status_code == status.HTTP_304_NOT_MODIFIED

    def test_entity_tags_keeps_the_prefix_when_asked_to(self):
        assert entity_tags(' W/"a", "b"', ignores_weak=False) == {'W/"a"', '"b"'}


@pytest.mark.django_db
@pytest.mark.usefixtures("option_urls_served", "atomic_requests")
class TestRequired:
    def test_off_lets_a_write_without_if_match_through_and_answers_with_its_version(self, client, widget):
        response = patch(client, f"/optional/{widget.pk}/")

        assert response.status_code == status.HTTP_200_OK
        assert response["ETag"] == etag(widget)
        assert Widget.objects.get().name == "nut"

    def test_off_still_checks_one_that_sends_it(self, client, widget):
        assert patch(client, f"/optional/{widget.pk}/", '"stale"').status_code == status.HTTP_412_PRECONDITION_FAILED

    def test_a_setting_turns_it_off_for_every_view(self, client, settings, widget):
        settings.ROOT_URLCONF = FINE
        settings.PRECONDITION_REQUIRED = False

        assert patch(client, f"/widgets/{widget.pk}/").status_code == status.HTTP_200_OK

    def test_off_reports_each_write_exempt_in_coverage(self):
        report = {(e.routed.route, e.routed.method): e for e in precondition_coverage(OPTIONS)}

        entry = report["^optional/(?P<pk>[^/.]+)/$", "PATCH"]
        assert entry.status is CoverageStatus.EXEMPT
        assert entry.reason == "If-Match is checked when sent, and not required - its precondition_required is off."
        assert report["^strict/(?P<pk>[^/.]+)/$", "PATCH"].status is CoverageStatus.COVERED


@pytest.mark.django_db
@pytest.mark.usefixtures("option_urls_served", "atomic_requests")
class TestAllowsAny:
    def test_off_refuses_any_with_428(self, client, widget):
        response = patch(client, f"/named-only/{widget.pk}/", "*")

        assert response.status_code == status.HTTP_428_PRECONDITION_REQUIRED
        assert response.json() == {
            "detail": "This request must name the version it was made against - If-Match: * isn't accepted.",
            "code": "precondition_required",
        }
        assert Widget.objects.get().name == "bolt"

    def test_off_refuses_any_in_a_list_too(self, client, widget):
        response = patch(client, f"/named-only/{widget.pk}/", f"{etag(widget)}, *")

        assert response.status_code == status.HTTP_428_PRECONDITION_REQUIRED

    def test_off_still_takes_the_version_named(self, client, widget):
        assert patch(client, f"/named-only/{widget.pk}/", etag(widget)).status_code == status.HTTP_200_OK

    def test_on_any_needs_no_lock_first_to_be_refused(self, client, widget):
        # Refused before the row is read again - nothing for a 412 to show.
        response = patch(client, f"/named-only/{uuid.uuid4()}/", "*")

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
@pytest.mark.usefixtures("option_urls_served", "atomic_requests")
class TestMissingRowStatus:
    def test_412_answers_if_match_on_a_missing_row_with_no_current(self, client):
        for sent in ("*", '"stale"'):
            response = patch(client, f"/gone-fails/{uuid.uuid4()}/", sent)

            assert response.status_code == status.HTTP_412_PRECONDITION_FAILED
            assert response.json() == {
                "detail": "This changed since the version the request was made against.",
                "code": "precondition_failed",
                "current": None,
            }
            assert "ETag" not in response

    def test_412_leaves_a_missing_row_without_if_match_a_404(self, client):
        assert patch(client, f"/gone-fails/{uuid.uuid4()}/").status_code == status.HTTP_404_NOT_FOUND

    def test_412_leaves_a_read_of_a_missing_row_a_404(self, client):
        response = client.get(f"/gone-fails/{uuid.uuid4()}/", headers={"If-Match": "*"})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_the_default_keeps_the_404s_own_message(self, settings, client):
        settings.ROOT_URLCONF = FINE

        response = patch(client, f"/widgets/{uuid.uuid4()}/", "*")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json() == {"detail": "No Widget matches the given query."}

    def test_412_for_a_row_deleted_between_the_read_and_the_lock(self, client, widget):
        response = patch(client, f"/gone-underfoot/{widget.pk}/", etag(widget))

        assert response.status_code == status.HTTP_412_PRECONDITION_FAILED
        assert response.json()["current"] is None

    def test_412_for_a_row_gone_before_the_lock(self, widget):
        Widget.objects.filter(pk=widget.pk).delete()

        with pytest.raises(PreconditionFailed) as raised:
            views.GoneFailsWidgetViewSet()._locked(widget, "*")

        assert raised.value.instance is None

    def test_the_default_for_a_row_gone_before_the_lock_is_404(self, widget):
        Widget.objects.filter(pk=widget.pk).delete()

        with pytest.raises(Http404):
            views.WidgetViewSet()._locked(widget, "*")


@pytest.mark.django_db
@pytest.mark.usefixtures("option_urls_served", "atomic_requests")
class TestNotModified:
    def test_off_answers_in_full_with_the_etag(self, client, widget):
        response = client.get(f"/always-in-full/{widget.pk}/", headers={"If-None-Match": etag(widget)})

        assert response.status_code == status.HTTP_200_OK
        assert response["ETag"] == etag(widget)
        assert response.json()["name"] == "bolt"


class TestTheCheck:
    def test_views_with_sensible_options_say_nothing(self):
        assert precondition_option_problems(FINE) == []
        assert precondition_option_warnings(FINE) == []

    def test_a_switch_that_isnt_true_or_false_is_an_error(self, settings):
        settings.PRECONDITION_REQUIRED = "yes"
        settings.PRECONDITION_NOT_MODIFIED = 1

        problems = precondition_option_problems(FINE)

        assert problems[:2] == [
            "WidgetViewSet's precondition option 'required' is 'yes' - PRECONDITION_REQUIRED and "
            "precondition_required take True or False.",
            "WidgetViewSet's precondition option 'not_modified' is 1 - PRECONDITION_NOT_MODIFIED and "
            "precondition_not_modified take True or False.",
        ]

    @pytest.mark.parametrize("name", ["ignores_weak_tags", "allows_any"])
    def test_each_switch_is_checked(self, settings, name):
        setattr(settings, f"PRECONDITION_{name.upper()}", None)

        assert f"'{name}' is None" in precondition_option_problems(FINE)[0]

    @pytest.mark.parametrize("value", [410, "412", True])
    def test_a_missing_row_status_but_404_or_412_is_an_error(self, settings, value):
        settings.PRECONDITION_MISSING_ROW_STATUS = value

        assert precondition_option_problems(FINE)[0] == (
            f"WidgetViewSet's precondition option 'missing_row_status' is {value!r} - "
            "PRECONDITION_MISSING_ROW_STATUS and precondition_missing_row_status take 404 or 412."
        )

    def test_412_is_a_missing_row_status_it_takes(self):
        assert precondition_option_problems(OPTIONS) == []

    def test_refusing_any_while_not_requiring_if_match_is_a_warning(self):
        assert precondition_option_warnings(OPTIONS) == [
            "OptionalNamedOnlyWidgetViewSet refuses If-Match: * but lets a write through without If-Match, so a "
            "client that would send * leaves the header out instead - refusing * protects nothing unless If-Match "
            "is required."
        ]

    def test_reports_errors_and_warnings(self, settings):
        settings.ROOT_URLCONF = OPTIONS
        settings.PRECONDITION_NOT_MODIFIED = "no"

        messages = every_precondition_view_has_options_that_make_sense(app_configs=None)

        # Every view but the one whose own precondition_not_modified overrides the setting.
        assert [(m.id, m.level) for m in messages] == [("isik.E003", 40)] * 6 + [("isik.W001", 30)]
        assert messages[0].msg == (
            "StrictWidgetViewSet's precondition option 'not_modified' is 'no' - PRECONDITION_NOT_MODIFIED and "
            "precondition_not_modified take True or False."
        )
        assert messages[-1].msg.startswith("OptionalNamedOnlyWidgetViewSet refuses If-Match: *")
        assert messages[0].hint == "See PreconditionMixin's options (isik.django.drf.viewsets.preconditions)."
        assert messages[-1].hint == messages[0].hint

    def test_says_nothing_without_drf(self, monkeypatch, settings):
        from django.apps import apps

        settings.ROOT_URLCONF = OPTIONS
        monkeypatch.setattr(apps, "is_installed", lambda name: name != "rest_framework")

        assert every_precondition_view_has_options_that_make_sense(app_configs=None) == []


@pytest.fixture
def schema():
    reset_generator_stats()
    with override_settings(REST_FRAMEWORK={"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}):
        generated = SchemaGenerator(patterns=option_urls.urlpatterns).get_schema(request=None, public=True)
    assert not GENERATOR_STATS._warn_cache
    return generated


def header_names(op):
    return {p["name"]: p for p in op.get("parameters", []) if p["in"] == "header"}


@pytest.mark.django_db
class TestTheSchema:
    def test_if_match_is_optional_and_there_is_no_428_when_not_required(self, schema):
        op = schema["paths"]["/optional/{id}/"]["patch"]

        assert "required" not in header_names(op)["If-Match"]
        assert "428" not in op["responses"]
        assert "412" in op["responses"]

    def test_refusing_any_keeps_the_428_even_when_not_required(self, schema):
        assert "428" in schema["paths"]["/optional-named-only/{id}/"]["patch"]["responses"]

    def test_a_412_for_a_missing_row_documents_current_as_nullable(self, schema):
        op = schema["paths"]["/gone-fails/{id}/"]["patch"]
        ref = op["responses"]["412"]["content"]["application/json"]["schema"]["$ref"]
        component = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]

        assert ref.endswith("/WidgetPreconditionFailedOrGone")
        assert component["properties"]["current"] == {
            "allOf": [{"$ref": "#/components/schemas/Widget"}],
            "nullable": True,
        }

    def test_the_default_412_documents_current_as_always_there(self, schema):
        ref = schema["paths"]["/strict/{id}/"]["patch"]["responses"]["412"]["content"]["application/json"]["schema"]

        assert ref == {"$ref": "#/components/schemas/WidgetPreconditionFailed"}

    def test_no_304_when_not_modified_is_off_but_still_the_etag(self, schema):
        op = schema["paths"]["/always-in-full/{id}/"]["get"]

        assert "If-None-Match" not in header_names(op)
        assert "304" not in op["responses"]
        assert "ETag" in op["responses"]["200"]["headers"]
