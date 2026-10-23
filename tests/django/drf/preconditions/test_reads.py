"""Every write that asks If-Match has a read handing out the version to send - checked at startup (isik.E004)
and, for a read that skips get_object(), when it answers."""

import sys
import uuid
from pathlib import Path

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from rest_framework import status
from rest_framework.viewsets import GenericViewSet

from isik.common.utils.exemptions import declared_exemptions
from isik.django.apps.common.checks import every_guarded_write_has_a_version_to_read
from isik.django.apps.common.db.concurrency import current_concurrency_etag
from isik.django.drf.viewsets import NoReadableVersion, PreconditionMixin, preconditions
from isik.django.drf.viewsets.preconditions import precondition_read_problems
from tests.django.drf.preconditions import read_views
from tests.testapp.models import CleanedNote, Widget


READS = "tests.django.drf.preconditions.read_urls"
FINE = "tests.django.drf.preconditions.urls"
HERE = str(Path(sys._getframe().f_code.co_filename).resolve())


def nothing_routed(write, method, route):
    return (
        f"{write} ({method} {route}) asks If-Match, and nothing routed hands out the version to send - no GET of "
        f"{route} sends an ETag, and there's no retrieve to read it from. Route one, name it in "
        "precondition_read_from, or exempt the write in precondition_unread_actions."
    )


class TestTheStartupCheck:
    def test_every_way_a_write_goes_unpaired(self):
        quiet = "^quiet/(?P<pk>[^/.]+)/$"

        assert precondition_read_problems(READS) == [
            "UnroutedReadViewSet.rename_me reads its version from 'nowhere' (precondition_read_from), which "
            "UnroutedReadViewSet doesn't route with GET.",
            "SilentReadViewSet.rename_me reads its version from 'peek' (precondition_read_from), which isn't in "
            "precondition_read_actions, so it sends no ETag.",
            nothing_routed("NoRetrieveViewSet.poke", "POST", "^no-retrieve/(?P<pk>[^/.]+)/poke/$"),
            nothing_routed("QuietRetrieveViewSet.update", "PUT", quiet),
            nothing_routed("QuietRetrieveViewSet.partial_update", "PATCH", quiet),
            nothing_routed("QuietRetrieveViewSet.destroy", "DELETE", quiet),
            "MixedWriteViewSet.update (PUT ^mixed/(?P<pk>[^/.]+)/$) reads its version from "
            "MixedReadViewSet.retrieve, which hands out a version of testapp.VersionedWidget, not of testapp.Widget.",
            "MixedWriteViewSet.partial_update (PATCH ^mixed/(?P<pk>[^/.]+)/$) reads its version from "
            "MixedReadViewSet.retrieve, which hands out a version of testapp.VersionedWidget, not of testapp.Widget.",
            "WidgetWriteView (PUT pair/<uuid:pk>/) reads its version from VersionedWidgetReadView, which hands out "
            "a version of testapp.VersionedWidget, not of testapp.Widget.",
            "WidgetWriteView (PATCH pair/<uuid:pk>/) reads its version from VersionedWidgetReadView, which hands "
            "out a version of testapp.VersionedWidget, not of testapp.Widget.",
        ]

    def test_a_detail_false_write_without_a_read_of_its_own(self):
        problems = precondition_read_problems(FINE)

        assert problems == [
            nothing_routed("WidgetViewSet.refuse_mine", "PATCH", "^widgets/refuse_mine/$"),
            nothing_routed("WidgetViewSet.rename_mine", "PATCH", "^widgets/rename_mine/$"),
            nothing_routed("WidgetViewSet.rename_mine_checked", "PATCH", "^widgets/rename_mine_checked/$"),
        ]

    def test_reports_each_as_an_error(self, settings):
        settings.ROOT_URLCONF = READS

        errors = every_guarded_write_has_a_version_to_read(app_configs=None)

        assert [error.id for error in errors] == ["isik.E004"] * 10
        assert errors[0].msg == precondition_read_problems(READS)[0]
        assert errors[0].hint == "See PreconditionMixin's version reads (isik.django.drf.viewsets.preconditions)."

    def test_says_nothing_without_drf(self, monkeypatch):
        monkeypatch.setattr(apps, "is_installed", lambda name: name != "rest_framework")
        monkeypatch.setattr(preconditions, "precondition_read_problems", lambda: ["one"])

        assert every_guarded_write_has_a_version_to_read(app_configs=None) == []

    def test_pairs_within_one_urlconf(self):
        # The write's read routed only by another urlconf is no read of the write's.
        assert nothing_routed("WidgetViewSet.update", "PUT", "^widgets/(?P<pk>[^/.]+)/$") not in (
            precondition_read_problems([FINE, READS])
        )


class TestDeclaring:
    def test_an_unread_write_says_why_and_is_listed(self):
        view, line = (
            type(
                "Unread", (PreconditionMixin, GenericViewSet), {"precondition_unread_actions": {"x": read_views.UNREAD}}
            ),
            sys._getframe().f_lineno,
        )

        made = view.precondition_unread_actions["x"]
        assert isinstance(made, NoReadableVersion)
        assert declared_exemptions("isik.precondition.readable-version")[-1] is made
        assert made.file == HERE
        # The statement's first line - where it begins, however it's wrapped.
        assert line - 3 <= made.line <= line

    def test_an_unread_write_needs_a_reason_of_some_length(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            type("Short", (PreconditionMixin,), {"precondition_unread_actions": {"x": "a script"}})

        assert str(raised.value).startswith(
            "Short.precondition_unread_actions - NoReadableVersion needs a reason of at least 40 characters."
        )

    def test_unread_writes_are_a_mapping(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            type("Listed", (PreconditionMixin,), {"precondition_unread_actions": ["x"]})

        assert str(raised.value) == "Listed.precondition_unread_actions takes {name: reason}, not ['x']."

    def test_read_from_is_a_mapping(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            type("Listed", (PreconditionMixin,), {"precondition_read_from": ["me"]})

        assert str(raised.value) == "Listed.precondition_read_from takes {write action: read action}, not ['me']."

    def test_neither_by_default(self):
        assert PreconditionMixin.precondition_read_from == PreconditionMixin.precondition_unread_actions == {}


@pytest.fixture
def reads(settings):
    settings.ROOT_URLCONF = READS


@pytest.fixture
def widget(alice):
    return Widget.objects.create(name="bolt", owner=alice)


@pytest.mark.django_db
@pytest.mark.usefixtures("reads", "atomic_requests")
class TestAReadThatSkipsGetObject:
    def test_a_declared_read_hands_out_the_version_its_write_names(self, client, widget):
        read = client.get("/me-widgets/me/")

        assert read["ETag"] == current_concurrency_etag(Widget, widget.pk)
        written = client.patch(
            "/me-widgets/rename_me/", {"name": "nut"}, format="json", headers={"If-Match": read["ETag"]}
        )
        assert written.status_code == status.HTTP_200_OK

    def test_one_named_a_read_is_refused(self, client, widget):
        with pytest.raises(ImproperlyConfigured) as raised:
            client.get("/me-widgets/me_unchecked/")

        assert str(raised.value) == (
            "MeViewSet answered GET me_unchecked without get_object(), so it sent no ETag - and a write needs one "
            "to name in If-Match. Fetch the row with self.get_object(), or take the action out of "
            "precondition_read_actions."
        )

    def test_a_head_is_refused_too(self, client, widget):
        with pytest.raises(ImproperlyConfigured):
            client.head("/me-widgets/me_unchecked/")

    def test_a_view_that_isnt_a_viewset_is_refused_where_its_url_names_one_object(self, client, widget):
        with pytest.raises(ImproperlyConfigured, match="^UncheckedWidgetView answered GET None"):
            client.get(f"/unchecked/{widget.pk}/")

    def test_a_failed_read_is_left_as_it_failed(self, client):
        # No widget of alice's - the action raises DoesNotExist, a 500 of its own, not the refusal.
        with pytest.raises(Widget.DoesNotExist):
            client.get("/me-widgets/me_unchecked/")

    def test_a_model_without_a_token_has_nothing_to_send(self, client):
        CleanedNote.objects.create(count=1)

        assert client.get("/unchecked-notes/me/").status_code == status.HTTP_200_OK

    def test_no_model_has_nothing_to_send(self, client):
        assert client.get("/modelless/me/").status_code == status.HTTP_200_OK

    def test_a_read_not_named_one_is_left_alone(self, client, widget, settings):
        settings.ROOT_URLCONF = FINE

        assert client.get("/widgets/mine/").status_code == status.HTTP_200_OK

    def test_a_list_of_a_view_that_isnt_a_viewset_is_left_alone(self, client, widget, settings):
        settings.ROOT_URLCONF = FINE

        assert client.get("/widget/").status_code == status.HTTP_200_OK

    def test_a_read_through_get_object_is_left_alone(self, client, widget):
        assert client.get(f"/me-widgets/{widget.pk}/").status_code == status.HTTP_200_OK

    def test_a_missing_row_is_still_a_404(self, client):
        assert client.get(f"/me-widgets/{uuid.uuid4()}/").status_code == status.HTTP_404_NOT_FOUND
