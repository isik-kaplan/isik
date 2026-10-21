"""What adopting PreconditionMixin in a real project turned up: lazy objects, writes that skip get_object(),
and views whose every write is exempt."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.utils.functional import SimpleLazyObject
from drf_spectacular.drainage import GENERATOR_STATS, reset_generator_stats
from drf_spectacular.generators import SchemaGenerator
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter
from rest_framework.viewsets import GenericViewSet

from isik.django.apps.common.db.concurrency import (
    assert_concurrency_field_changes,
    concurrency_etag,
    current_concurrency_etag,
    has_concurrency_token,
)
from isik.django.drf.coverage import CoverageStatus, precondition_coverage
from isik.django.drf.serializers import ConcurrencyTokenField
from isik.django.drf.viewsets import PreconditionMixin
from isik.django.drf.viewsets.preconditions import precondition_problems
from tests.django.drf.preconditions import urls
from tests.testapp.models import CleanedNote, VersionedNote, Widget


FINE = "tests.django.drf.preconditions.urls"


@pytest.fixture
def widget(alice):
    return Widget.objects.create(name="bolt", owner=alice)


def etag(widget):
    return current_concurrency_etag(Widget, widget.pk)


class TestALazyObject:
    def test_its_etag_is_its_wrapped_objects(self):
        assert concurrency_etag(SimpleLazyObject(lambda: VersionedNote(version=3))) == '"3"'

    def test_the_field_reads_through_it(self):
        assert ConcurrencyTokenField().to_representation(SimpleLazyObject(lambda: VersionedNote(version=3))) == '"3"'

    def test_whether_it_has_a_token_is_its_wrapped_objects(self):
        assert has_concurrency_token(SimpleLazyObject(lambda: VersionedNote(version=3)))
        assert not has_concurrency_token(SimpleLazyObject(lambda: CleanedNote(count=1)))

    @pytest.mark.django_db
    def test_the_test_helper_takes_one(self):
        note = VersionedNote.objects.create(text="a")

        assert_concurrency_field_changes(SimpleLazyObject(lambda: note))

        note.refresh_from_db()
        assert note.version == 4

    @pytest.mark.django_db
    @pytest.mark.usefixtures("urls", "atomic_requests")
    def test_a_serializer_shows_one_as_request_user_is(self, client, widget):
        response = client.get("/widgets/mine/")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["etag"] == etag(widget)


@pytest.mark.django_db
@pytest.mark.usefixtures("urls", "atomic_requests")
class TestAWriteThatSkipsGetObject:
    def test_succeeding_unchecked_is_refused_and_rolled_back(self, client, widget):
        with pytest.raises(ImproperlyConfigured) as raised:
            client.patch("/widgets/rename_mine/", {"name": "nut"}, format="json", headers={"If-Match": '"stale"'})

        assert str(raised.value) == (
            "WidgetViewSet answered PATCH rename_mine without comparing If-Match, so its database writes are "
            "rolled back. Fetch the row with self.get_object(), which compares it, or exempt the action in "
            "precondition_exempt_actions."
        )
        assert Widget.objects.get().name == "bolt"

    def test_a_post_to_one_object_is_refused_too(self, client, widget):
        with pytest.raises(ImproperlyConfigured):
            client.post(f"/widgets/{widget.pk}/bump_unchecked/", headers={"If-Match": etag(widget)})

        assert Widget.objects.get().count == 0

    def test_failing_unchecked_is_left_as_it_failed(self, client, widget):
        response = client.patch("/widgets/refuse_mine/", {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_exempt_it_goes_through(self, client, widget):
        response = client.patch("/widgets/rename_mine_freely/", {"name": "nut"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert Widget.objects.get().name == "nut"

    def test_fetching_its_row_through_get_object_compares_if_match(self, client, widget):
        stale = client.patch(
            "/widgets/rename_mine_checked/", {"name": "nut"}, format="json", headers={"If-Match": '"stale"'}
        )
        missing = client.patch("/widgets/rename_mine_checked/", {"name": "nut"}, format="json")
        current = client.patch(
            "/widgets/rename_mine_checked/", {"name": "nut"}, format="json", headers={"If-Match": etag(widget)}
        )

        assert (stale.status_code, missing.status_code, current.status_code) == (412, 428, 200)
        assert current["ETag"] == etag(widget)

    def test_with_if_match_optional_it_is_still_refused(self, client, widget, settings):
        # A sent If-Match would be ignored all the same.
        settings.PRECONDITION_REQUIRED = False

        with pytest.raises(ImproperlyConfigured):
            client.patch("/widgets/rename_mine/", {"name": "nut"}, format="json")

    def test_coverage_lists_it_as_asking_for_if_match(self):
        report = {(e.routed.route, e.routed.method): e.status for e in precondition_coverage(FINE)}

        assert report["^widgets/rename_mine/$", "PATCH"] is CoverageStatus.COVERED
        assert report["^widgets/rename_mine_freely/$", "PATCH"] is CoverageStatus.EXEMPT


@pytest.mark.django_db
@pytest.mark.usefixtures("urls", "atomic_requests")
class TestAViewWhoseEveryWriteIsExempt:
    def test_its_model_needs_no_token(self):
        assert precondition_problems(FINE) == []

    def test_its_writes_go_through_and_send_no_etag(self, client):
        note = CleanedNote.objects.create(count=1)

        touched = client.post(f"/exempt-notes/{note.pk}/touch/", format="json")
        patched = client.patch(f"/exempt-notes/{note.pk}/", {"count": 2}, format="json")
        created = client.post("/exempt-notes/", {"count": 3}, format="json")

        assert (touched.status_code, patched.status_code, created.status_code) == (200, 200, 201)
        assert "ETag" not in touched
        assert "ETag" not in patched
        assert "ETag" not in created

    def test_its_reads_send_none_and_ignore_if_none_match(self, client):
        note = CleanedNote.objects.create(count=1)

        response = client.get(f"/exempt-notes/{note.pk}/", headers={"If-None-Match": "*"})

        assert response.status_code == status.HTTP_200_OK
        assert "ETag" not in response

    def test_coverage_lists_its_writes_exempt(self):
        report = {(e.routed.route, e.routed.method): e for e in precondition_coverage(FINE)}

        entry = report["^exempt-notes/(?P<pk>[^/.]+)/touch/$", "POST"]
        assert entry.status is CoverageStatus.EXEMPT
        assert entry.reason == "notes are written last-one-wins, and their model keeps no version to compare"


class PokeSerializer(serializers.Serializer):
    poked = serializers.BooleanField()


class ModellessViewSet(PreconditionMixin, GenericViewSet):
    serializer_class = PokeSerializer
    precondition_exempt_actions = {"poke": "answers a device code, writing no versioned row at all"}

    @action(detail=True, methods=["post"])
    def poke(self, request, pk=None):
        return Response({"poked": True})


def generate(patterns, *, warns=False):
    reset_generator_stats()
    with override_settings(REST_FRAMEWORK={"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}):
        generated = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
    assert bool(GENERATOR_STATS._warn_cache) is warns
    return generated


@pytest.mark.django_db
class TestTheSchemaOfAViewWithNoToken:
    def test_a_model_without_one_documents_no_etag(self):
        paths = generate(urls.urlpatterns)["paths"]

        for op in (paths["/exempt-notes/{id}/"]["get"], paths["/exempt-notes/{id}/"]["patch"]):
            assert not [p for p in op.get("parameters", []) if p["in"] == "header"]
            assert not {"304", "412", "428"} & set(op["responses"])
            assert "headers" not in op["responses"]["200"]

    def test_no_model_documents_no_etag(self):
        router = SimpleRouter()
        router.register("modelless", ModellessViewSet, basename="modelless")

        # drf-spectacular warns that it can't type a modelless view's {id} - its own concern, not isik's.
        op = generate(router.urls, warns=True)["paths"]["/modelless/{id}/poke/"]["post"]

        assert set(op["responses"]) == {"200"}
        assert "headers" not in op["responses"]["200"]
