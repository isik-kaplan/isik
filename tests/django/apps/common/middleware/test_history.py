import pghistory
import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory

from isik.django.apps.common.db.history import open_history_context
from isik.django.apps.common.middleware import HistoryContextMiddleware
from tests.testapp.models import EmailUser, Widget, WidgetEvent


@pytest.fixture
def rf():
    return RequestFactory()


def serve(request, view, middleware_class=HistoryContextMiddleware):
    """Runs `view` as the response the middleware wraps, returning what the view returned."""
    return middleware_class(get_response=view)(request)


def test_the_context_is_readable_while_the_request_is_served(rf):
    request = rf.post("/widgets/")
    request.user = AnonymousUser()

    seen = serve(request, lambda request: open_history_context())

    assert seen == {"user": None, "url": "/widgets/"}


def test_nothing_is_open_outside_a_request():
    assert open_history_context() is None


def test_nothing_is_left_open_once_the_request_is_served(rf):
    serve(rf.post("/widgets/"), lambda request: HttpResponse())

    assert open_history_context() is None


def test_nothing_is_left_open_when_the_view_raises(rf):
    def view(request):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="^boom$"):
        serve(rf.post("/widgets/"), view)

    assert open_history_context() is None


@pytest.mark.django_db
def test_a_user_authenticated_after_the_middleware_ran_is_seen(rf):
    # DRF sets request.user in the view, after get_context() has run - pghistory's request class adds
    # it to the open context then, and the reader has to be looking at that context, not a snapshot.
    user = EmailUser.objects.create(username="alice", email="alice@example.com")
    request = rf.post("/widgets/")
    request.user = AnonymousUser()

    def view(request):
        request.user = user
        return open_history_context()

    assert serve(request, view)["user"] == user.pk


def test_keys_a_view_adds_are_seen(rf):
    def view(request):
        pghistory.context(endpoint="widget-list")
        return open_history_context()

    assert serve(rf.post("/widgets/"), view)["endpoint"] == "widget-list"


def test_a_subclass_adds_keys_the_usual_way(rf):
    class TenantHistoryContextMiddleware(HistoryContextMiddleware):
        def get_context(self, request):
            return {**super().get_context(request), "schema": "acme"}

    seen = serve(rf.post("/widgets/"), lambda request: open_history_context(), TenantHistoryContextMiddleware)

    assert seen["schema"] == "acme"


def test_the_reader_returns_a_copy(rf):
    def view(request):
        open_history_context()["user"] = "someone else"
        return open_history_context()

    assert serve(rf.post("/widgets/"), view)["user"] is None


def test_a_method_pghistory_does_not_track_opens_nothing(rf, settings):
    settings.PGHISTORY_MIDDLEWARE_METHODS = ("POST",)

    response = serve(rf.get("/widgets/"), lambda request: HttpResponse(repr(open_history_context())))

    assert response.content == b"None"


def test_an_already_open_context_is_joined_and_left_open(rf):
    with pghistory.context(command="import") as surrounding:
        seen = serve(rf.post("/widgets/"), lambda request: open_history_context())
        # Only joins an open context - so this lands only if the middleware left that one open.
        pghistory.context(after="request")

    assert seen["command"] == "import"
    assert seen["url"] == "/widgets/"
    assert surrounding.metadata["after"] == "request"


@pytest.mark.django_db
def test_rows_written_while_serving_still_carry_the_context(rf):
    # The context this middleware opens first, empty, is the one pghistory's own then fills - a row
    # has to come out annotated exactly as it would under the plain HistoryMiddleware.
    def view(request):
        return Widget.objects.create(name="bolt")

    widget = serve(rf.post("/widgets/"), view)

    event = WidgetEvent.objects.get(pgh_obj_id=widget.pk)
    assert event.pgh_context.metadata == {"user": None, "url": "/widgets/"}


class TestOptionalKeys:
    def seen(self, rf, **headers):
        request = rf.post("/", headers=headers)
        request.user = AnonymousUser()
        return serve(request, lambda request: open_history_context())

    def test_none_by_default(self, rf, settings):
        assert set(self.seen(rf)) == {"url", "user"}

    def test_the_version_named(self, rf, settings):
        settings.HISTORY_CONTEXT_KEYS = ["version"]
        settings.HISTORY_CONTEXT_VERSION = "1.2.3"

        assert self.seen(rf)["version"] == "1.2.3"

    def test_the_request_id_a_proxy_sent(self, rf, settings):
        settings.HISTORY_CONTEXT_KEYS = ["request_id"]

        assert self.seen(rf, **{"X-Request-ID": "abc"})["request_id"] == "abc"

    def test_the_request_id_header_is_a_setting(self, rf, settings):
        settings.HISTORY_CONTEXT_KEYS = ["request_id"]
        settings.HISTORY_REQUEST_ID_HEADER = "X-Trace"

        assert self.seen(rf, **{"X-Trace": "abc"})["request_id"] == "abc"

    def test_a_request_id_is_made_when_none_was_sent(self, rf, settings):
        settings.HISTORY_CONTEXT_KEYS = ["request_id"]

        first, second = self.seen(rf)["request_id"], self.seen(rf)["request_id"]

        assert len(first) == 32 and first != second

    def test_an_unknown_key_is_refused(self, settings):
        settings.HISTORY_CONTEXT_KEYS = ["version", "build", "commit"]
        settings.HISTORY_CONTEXT_VERSION = "1.2.3"

        with pytest.raises(ImproperlyConfigured) as raised:
            HistoryContextMiddleware(get_response=lambda request: None)

        assert str(raised.value) == "HISTORY_CONTEXT_KEYS names build, commit - it takes version, request_id."

    def test_the_version_needs_its_setting(self, settings):
        settings.HISTORY_CONTEXT_KEYS = ["version"]

        with pytest.raises(ImproperlyConfigured) as raised:
            HistoryContextMiddleware(get_response=lambda request: None)

        assert str(raised.value) == "HISTORY_CONTEXT_KEYS names version, and HISTORY_CONTEXT_VERSION isn't set."
