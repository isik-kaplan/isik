import uuid

import pghistory
import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory

from isik.django.apps.common.db.history import open_history_context
from isik.django.apps.common.middleware import HistoryContextMiddleware
from isik.django.apps.common.middleware.history import is_uuid
from isik.django.conf import isik_with
from tests.testapp.models import EmailUser, Widget, WidgetEvent


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture(autouse=True)
def unanswered(settings):
    # The views here return what they saw, not a response - so nothing is answered with an id, but where a test asks.
    settings.ISIK = isik_with(DJANGO={"HISTORY": {"RETURN_REQUEST_ID": False}})


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
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["version"]}})
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_VERSION": "1.2.3"}})

        assert self.seen(rf)["version"] == "1.2.3"

    def test_the_version_and_its_build(self, rf, settings):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["version", "build"]}})
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_VERSION": "1.2.3"}})
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_BUILD": "5c49407"}})

        seen = self.seen(rf)

        assert (seen["version"], seen["build"]) == ("1.2.3", "5c49407")

    def test_the_build_needs_its_setting(self, settings):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["build"]}})

        with pytest.raises(ImproperlyConfigured) as raised:
            HistoryContextMiddleware(get_response=lambda request: None)

        assert (
            str(raised.value)
            == "ISIK['DJANGO']['HISTORY']['CONTEXT_KEYS'] names build, and ISIK['DJANGO']['HISTORY']['CONTEXT_BUILD'] "
            "isn't set."
        )

    def test_the_request_id_is_made_here_for_each_request(self, rf, settings):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["request_id"]}})
        sent = str(uuid.uuid4())

        first, second = self.seen(rf, **{"X-Request-ID": sent})["request_id"], self.seen(rf)["request_id"]

        assert first != second and sent not in (first, second)
        assert uuid.UUID(first).version == 7

    def test_the_correlation_id_a_proxy_sent(self, rf, settings):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["request_id", "correlation_id"]}})
        sent = "0123456789abcdef0123456789abcdef"

        seen = self.seen(rf, **{"X-Request-ID": sent})

        assert seen["correlation_id"] == sent != seen["request_id"]

    def test_with_none_sent_the_correlation_id_is_the_requests_own(self, rf, settings):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["request_id", "correlation_id"]}})

        seen = self.seen(rf)

        assert seen["correlation_id"] == seen["request_id"]

    @pytest.mark.parametrize("sent", ["abc", "x" * 5000, "0123456789abcdef0123456789abcde\n", ""])
    def test_an_id_that_isnt_one_is_replaced(self, rf, settings, sent):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["request_id", "correlation_id"]}})

        seen = self.seen(rf, **{"X-Request-ID": sent})

        assert seen["correlation_id"] == seen["request_id"]

    def test_the_header_is_a_setting(self, rf, settings):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["correlation_id"]}})
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"REQUEST_ID_HEADER": "X-Trace"}})
        sent = str(uuid.uuid4())

        assert self.seen(rf, **{"X-Trace": sent, "X-Request-ID": str(uuid.uuid4())})["correlation_id"] == sent

    @pytest.mark.parametrize(
        "valid", [lambda value: value.startswith("req-"), "tests.django.apps.common.middleware.test_history.starts_req"]
    )
    def test_what_an_id_is_is_a_setting(self, rf, settings, valid):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["correlation_id"], "REQUEST_ID_VALID": valid}})

        assert self.seen(rf, **{"X-Request-ID": "req-7"})["correlation_id"] == "req-7"
        assert self.seen(rf, **{"X-Request-ID": str(uuid.uuid4())})["correlation_id"].startswith("req-") is False

    def test_the_sentry_trace(self, rf, settings):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["trace_id"]}})
        trace = "771a43a4192642f0b136d5159a501700"

        assert self.seen(rf, **{"sentry-trace": f"{trace}-a3d6c0f2b3e8f1d2-1"})["trace_id"] == trace
        assert self.seen(rf, **{"sentry-trace": "nonsense"})["trace_id"] is None
        assert self.seen(rf)["trace_id"] is None

    def test_no_sentry_trace_is_no_trace_whatever_an_id_may_be(self, rf, settings):
        valid = {"CONTEXT_KEYS": ["trace_id"], "REQUEST_ID_VALID": lambda value: True}
        settings.ISIK = isik_with(DJANGO={"HISTORY": valid})

        assert self.seen(rf)["trace_id"] is None

    def test_ids_named_alone_are_the_only_ones_kept(self, rf, settings):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["trace_id"]}})

        assert set(self.seen(rf)) == {"url", "user", "trace_id"}

    def test_an_unknown_key_is_refused(self, settings):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["version", "sha", "commit"]}})
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_VERSION": "1.2.3"}})

        with pytest.raises(ImproperlyConfigured) as raised:
            HistoryContextMiddleware(get_response=lambda request: None)

        assert (
            str(raised.value)
            == "ISIK['DJANGO']['HISTORY']['CONTEXT_KEYS'] names commit, sha - it takes version, build, request_id, "
            "correlation_id, trace_id."
        )

    def test_the_version_needs_its_setting(self, settings):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["version"]}})

        with pytest.raises(ImproperlyConfigured) as raised:
            HistoryContextMiddleware(get_response=lambda request: None)

        assert (
            str(raised.value) == "ISIK['DJANGO']['HISTORY']['CONTEXT_KEYS'] names version, and "
            "ISIK['DJANGO']['HISTORY']['CONTEXT_VERSION'] isn't set."
        )


def starts_req(value):
    return value.startswith("req-")


class TestEveryRequestsIds:
    def test_a_context_built_outside_the_request_cycle_has_them_too(self, rf, settings):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"CONTEXT_KEYS": ["request_id", "correlation_id"]}})
        sent = str(uuid.uuid4())
        request = rf.post("/", headers={"X-Request-ID": sent})
        request.user = AnonymousUser()

        context = HistoryContextMiddleware(get_response=None).get_context(request)

        assert context["correlation_id"] == sent
        assert context["request_id"] == request.request_id
        assert uuid.UUID(request.request_id).version == 7

    def test_ids_a_request_has_are_kept(self, rf):
        request = rf.post("/")
        request.request_id, request.correlation_id = "mine", "theirs"

        HistoryContextMiddleware.identify(request)

        assert (request.request_id, request.correlation_id) == ("mine", "theirs")

    def test_every_request_carries_them_whatever_its_method(self, rf, settings):
        settings.PGHISTORY_MIDDLEWARE_METHODS = ("POST",)
        sent = str(uuid.uuid4())

        seen = serve(rf.get("/", headers={"X-Request-ID": sent}), lambda request: request)

        assert seen.correlation_id == sent
        assert uuid.UUID(seen.request_id).version == 7

    @pytest.mark.parametrize("method", ["get", "post"])
    def test_the_response_answers_with_the_correlation_id(self, rf, settings, method):
        settings.ISIK = isik_with(DJANGO={"HISTORY": {"RETURN_REQUEST_ID": True}})
        settings.PGHISTORY_MIDDLEWARE_METHODS = ("POST",)
        sent = str(uuid.uuid4())
        request = getattr(rf, method)("/", headers={"X-Request-ID": sent})
        request.user = AnonymousUser()

        assert serve(request, lambda request: HttpResponse())["X-Request-ID"] == sent

    def test_answered_by_default_under_the_header_set(self, rf, settings):
        history = {**settings.ISIK["DJANGO"]["HISTORY"], "REQUEST_ID_HEADER": "X-Trace"}
        del history["RETURN_REQUEST_ID"]
        settings.ISIK = {**settings.ISIK, "DJANGO": {**settings.ISIK["DJANGO"], "HISTORY": history}}
        request = rf.post("/")
        request.user = AnonymousUser()

        response = serve(request, lambda request: HttpResponse())

        assert response["X-Trace"] == request.correlation_id == request.request_id
        assert "X-Request-ID" not in response

    def test_not_answered_when_turned_off(self, rf):
        request = rf.post("/")
        request.user = AnonymousUser()

        assert "X-Request-ID" not in serve(request, lambda request: HttpResponse())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0123456789abcdef0123456789abcdef", True),
        ("12345678-1234-5678-1234-567812345678", True),
        ("{12345678-1234-5678-1234-567812345678}", False),
        ("{12345678-1234-5678-1234-567812345678", False),
        ("12345678-1234-5678-1234-56781234567", False),
        ("0123456789abcdef0123456789abcde\n", False),
        (" 0123456789abcdef0123456789abcdef", False),
        ("urn:uuid:12345678-1234-5678-1234-567812345678", False),
        ("not-a-uuid", False),
    ],
)
def test_is_uuid(value, expected):
    assert is_uuid(value) is expected
