"""Push beyond the protocol's fixtures: its backend, its entries' requests, its limits and throttles."""

import logging
import uuid

import pghistory
import pytest
from django.core.cache import cache
from django.test import RequestFactory, override_settings
from pghistory import runtime
from rest_framework.authentication import SessionAuthentication
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle

from isik.django.apps.sync import push as pushing
from isik.django.apps.sync.drf import PerEntryThrottle, SyncViewSet
from isik.django.apps.sync.push import (
    KEY_NAMESPACE,
    PushBackend,
    ViewPushBackend,
    entry_context,
    entry_key,
    result,
    route,
    sub_request,
)
from isik.django.conf import override_isik
from tests.django.apps.sync import support
from tests.testapp.models import SyncTask
from tests.testapp.sync_views import TaskViewSet


ALICE = "0190a000-0000-7000-8000-a00000000001"
TASK = "0190a000-0000-7000-8000-100000000001"
HEADERS = {"Isik-Sync-Protocol": "1"}


def created(key="k", number=1, **body):
    task_id = f"0190a000-0000-7000-8000-1000000000{number:02}"
    return {
        "key": key,
        "method": "POST",
        "model": "tasks",
        "id": task_id,
        "body": {"owner": ALICE, "title": "t", **body},
    }


@pytest.fixture
def client():
    made = APIClient()
    made.force_authenticate(support.user(ALICE))
    with override_settings(ROOT_URLCONF="tests.testapp.sync_urls"):
        yield made


def pushed(client, *entries, **headers):
    return client.post("/sync/push", {"entries": list(entries)}, format="json", headers={**HEADERS, **headers})


class TestAnEntrysKey:
    def test_a_uuid_is_itself(self):
        key = str(uuid.uuid4())

        assert entry_key(key.upper()) == key

    def test_anything_else_is_the_uuid_it_names(self):
        assert entry_key("k1") == str(uuid.uuid5(KEY_NAMESPACE, "k1")) == entry_key("k1")
        assert KEY_NAMESPACE == uuid.uuid5(uuid.NAMESPACE_URL, "isik.django.apps.sync.push")


class TestAnEntrysHistoryContext:
    def test_its_own_beside_the_request_s(self):
        with pghistory.context(user="alice") as outer:
            with entry_context(3):
                inner = runtime._tracker.value
            assert runtime._tracker.value is outer

        assert isinstance(inner.id, uuid.UUID)
        assert inner.id != outer.id
        assert inner.metadata == {"user": "alice", "sync_entry": 3}
        assert outer.metadata == {"user": "alice"}

    def test_its_own_with_no_request_s(self):
        with entry_context(0):
            assert runtime._tracker.value.metadata == {"sync_entry": 0}

        assert not hasattr(runtime._tracker, "value")

    def test_put_back_when_the_entry_raises(self):
        with pghistory.context() as outer, pytest.raises(ValueError), entry_context(1):
            raise ValueError()

        assert runtime._tracker.value is outer if hasattr(runtime._tracker, "value") else True


class TestABackend:
    def test_advertised_by_its_path_unless_named(self):
        assert ViewPushBackend({}).advertised_name == "isik.django.apps.sync.push.ViewPushBackend"
        assert type("Named", (PushBackend,), {"name": "queued"})({}).advertised_name == "queued"

    def test_its_options(self):
        backend = PushBackend({"MAX_ENTRIES": 2, "MAX_BYTES": 3, "THROTTLE_SCOPE": "s"})

        assert (backend.max_entries, backend.max_bytes, backend.throttle_scope) == (2, 3, "s")
        assert (PushBackend({}).max_entries, PushBackend({}).max_bytes, PushBackend({}).throttle_scope) == (None,) * 3

    def test_runs_nothing_itself(self):
        with pytest.raises(NotImplementedError):
            PushBackend({}).run(None, [])


class TestAResult:
    def test_a_success_with_no_body_and_no_row(self):
        assert result({"key": "k"}, Response(status=204)) == {"key": "k", "status": 204}

    def test_a_precondition_failed_with_no_current_and_no_etag(self):
        assert result({"key": "k"}, Response({"current": None}, status=412)) == {"key": "k", "status": 412}

    def test_any_other_refusal_carries_its_body(self):
        assert result({"key": "k"}, Response({"detail": "x"}, status=403)) == {
            "key": "k",
            "status": 403,
            "errors": {"detail": "x"},
        }


class TestARoute:
    def routes(self, urlconf="tests.testapp.sync_urls"):
        from rest_framework.schemas.generators import EndpointEnumerator

        return [(path, callback) for path, _, callback in EndpointEnumerator(urlconf=urlconf).get_api_endpoints()]

    def test_is_where_the_viewset_serves_the_action(self):
        assert route(TaskViewSet, self.routes(), "create") == "/api/tasks/"
        assert route(TaskViewSet, self.routes(), "partial_update") == "/api/tasks/{pk}/"
        assert route(TaskViewSet, self.routes(), "destroy") == "/api/tasks/{pk}/"

    def test_none_for_a_viewset_routed_nowhere(self):
        assert route(TaskViewSet, self.routes("tests.testapp.sync_urls_without_tasks"), "create") is None


@pytest.mark.django_db
class TestAnEntrysRequest:
    def test_is_the_push_s_with_the_entry_s_own_parts(self):
        outer = RequestFactory().post("/sync/push", HTTP_IF_MATCH='"old"', HTTP_X_TENANT="acme")
        outer.tenant = "acme"
        request = Request(outer)
        request.user, request.auth = support.user(ALICE), "token"
        entry = {"key": "k", "method": "PATCH", "model": "tasks", "id": TASK}

        made = sub_request(request, entry, f"/api/tasks/{TASK}/", {"done": True})

        assert (made.method, made.path, made.body) == ("PATCH", f"/api/tasks/{TASK}/", b'{"done":true}')
        assert made.headers["Idempotency-Key"] == entry_key("k")
        assert "If-Match" not in made.headers
        assert (made.headers["X-Tenant"], made.tenant) == ("acme", "acme")
        assert (made._force_auth_user, made._force_auth_token) == (request.user, "token")

    def test_carries_the_entry_s_if_match_and_no_body(self):
        request = Request(RequestFactory().post("/sync/push"))
        request.user, request.auth = None, None
        entry = {"key": "k", "method": "DELETE", "model": "tasks", "id": TASK, "if_match": '"v"'}

        made = sub_request(request, entry, "/x/", None)

        assert (made.headers["If-Match"], made.body, made.headers["Content-Length"]) == ('"v"', b"", "0")


@pytest.mark.django_db
class TestPushing:
    def test_an_entry_raising_is_its_own_500_and_writes_nothing(self, client, monkeypatch, caplog):
        def broken(self, serializer):
            serializer.save()
            raise RuntimeError("broken")

        monkeypatch.setattr(TaskViewSet, "perform_create", broken)
        with caplog.at_level(logging.ERROR, logger="isik.django.apps.sync.push"):
            response = pushed(client, created())

        assert response.json() == {"results": [{"key": "k", "status": 500, "errors": {"code": "error"}}]}
        assert not SyncTask.objects.exists()
        assert caplog.messages == ["Push entry k (POST tasks) raised."]

    def test_an_entry_refused_commits_nothing_and_the_next_runs(self, client):
        response = pushed(client, created("a", 1, title=""), created("b", 2))

        assert [each["status"] for each in response.json()["results"]] == [400, 201]
        assert list(SyncTask.objects.values_list("title", flat=True)) == ["t"]

    def test_a_returned_refusal_rolls_back_what_the_view_wrote(self, client, monkeypatch):
        def refusing(self, request, *args, **kwargs):
            SyncTask.objects.create(id=TASK, owner_id=ALICE, title="written")
            return Response({"detail": "no"}, status=403)

        monkeypatch.setattr(TaskViewSet, "create", refusing)
        response = pushed(client, created())

        assert response.json()["results"] == [{"key": "k", "status": 403, "errors": {"detail": "no"}}]
        assert not SyncTask.objects.exists()

    def test_runs_a_view_s_per_entry_throttles_and_no_other(self, client, monkeypatch):
        class Texts(PerEntryThrottle, UserRateThrottle):
            scope = "texts"

        class Requests(UserRateThrottle):
            scope = "requests"

        monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", {"texts": "1/min", "requests": "0/min"})
        cache.clear()
        monkeypatch.setattr(TaskViewSet, "throttle_classes", [Texts, Requests])
        response = pushed(client, created("a", 1), created("b", 2))

        assert [each["status"] for each in response.json()["results"]] == [201, 429]
        cache.clear()

    def test_a_body_that_isn_t_a_push_is_a_400(self, client):
        response = client.post("/sync/push", {"entries": [{"key": "k"}]}, format="json", headers=HEADERS)

        assert response.status_code == 400
        assert set(response.json()["entries"][0]) == {"method", "model", "id"}

    def test_a_body_over_max_bytes_is_a_413(self, client):
        with override_isik(DJANGO={"SYNC": {"PUSH": {"OPTIONS": {"MAX_BYTES": 10}}}}):
            response = pushed(client, created())

        assert (response.status_code, response.json()) == (
            413,
            {"code": "sync_push_too_large", "max_entries": None, "max_bytes": 10},
        )

    def test_at_its_limits_goes_through(self, client):
        entry = created()
        size = len(APIClient()._encode_data({"entries": [entry]}, format="json")[0])
        with override_isik(DJANGO={"SYNC": {"PUSH": {"OPTIONS": {"MAX_BYTES": size, "MAX_ENTRIES": 1}}}}):
            response = pushed(client, entry)

        assert response.status_code == 200

    def test_a_backend_of_the_project_s_runs_it(self, client):
        with override_isik(DJANGO={"SYNC": {"PUSH": {"BACKEND": Echo}}}):
            response = pushed(client, created())
            capabilities = client.get("/sync/capabilities", headers=HEADERS).json()

        assert response.json() == {"results": [{"key": "k", "status": 202}]}
        assert capabilities["push"]["backend"] == "echo"


class Echo(PushBackend):
    name = "echo"

    def run(self, request, entries):
        return [{"key": entry["key"], "status": 202} for entry in entries]


@pytest.mark.django_db
class TestPushsThrottle:
    @pytest.fixture(autouse=True)
    def rated(self, monkeypatch):
        monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", {"sync_push": "3/min"})
        cache.clear()
        with override_isik(DJANGO={"SYNC": {"PUSH": {"OPTIONS": {"THROTTLE_SCOPE": "sync_push"}}}}):
            yield
        cache.clear()

    def test_counts_entries(self, client):
        assert pushed(client, created("a", 1), created("b", 2)).status_code == 200
        assert pushed(client, created("c", 3), created("d", 4)).status_code == 429
        assert pushed(client, created("c", 3)).status_code == 200
        assert pushed(client, created("e", 5)).status_code == 429

    def test_a_body_with_no_entries_counts_one(self, client):
        for _ in range(3):
            assert client.post("/sync/push", {"entries": []}, format="json", headers=HEADERS).status_code == 200
        assert client.post("/sync/push", [], format="json", headers=HEADERS).status_code == 429

    def test_forgets_what_s_its_period_old(self, client, monkeypatch):
        moments = iter([0, 0, 60])
        monkeypatch.setattr("rest_framework.throttling.SimpleRateThrottle.timer", lambda self: next(moments))

        assert pushed(client, created("a", 1), created("b", 2), created("c", 3)).status_code == 200
        assert pushed(client, created("d", 4)).status_code == 429
        assert pushed(client, created("d", 4)).status_code == 200

    def test_counts_each_person_s_apart(self, client):
        assert pushed(client, created("a", 1), created("b", 2), created("c", 3)).status_code == 200
        bob = APIClient()
        bob.force_authenticate(support.user("0190a000-0000-7000-8000-a00000000002"))

        assert pushed(bob, created("d", 4)).status_code == 200

    def test_the_feed_and_capabilities_aren_t_push_s(self, client):
        for _ in range(4):
            assert client.get("/sync/capabilities", headers=HEADERS).status_code == 200


class HostRealm(SessionAuthentication):
    def authenticate_header(self, request):
        return f'Host realm="{request.get_host()}"'


@pytest.mark.django_db
class TestTheEndpoints:
    def test_answer_a_stranger_with_how_their_authentication_asks(self, monkeypatch):
        monkeypatch.setattr(SyncViewSet, "authentication_classes", [HostRealm])
        with override_settings(ROOT_URLCONF="tests.testapp.sync_urls"):
            response = APIClient().get("/sync/changes", headers=HEADERS)

        assert (response.status_code, response["WWW-Authenticate"]) == (401, 'Host realm="testserver"')

    def test_answer_a_stranger_401_however_they_d_authenticate(self, monkeypatch):
        monkeypatch.setattr(SyncViewSet, "authentication_classes", [SessionAuthentication])
        with override_settings(ROOT_URLCONF="tests.testapp.sync_urls"):
            response = APIClient().get("/sync/changes", headers=HEADERS)

        assert (response.status_code, response["WWW-Authenticate"]) == (401, 'Session realm="api"')

    def test_name_their_protocol_on_every_answer(self, client):
        response = client.get("/sync/changes")

        assert (response.status_code, response["Isik-Sync-Protocol"]) == (400, "1")


def test_the_module_reads_its_backend_from_the_setting():
    assert pushing.ACTIONS["PATCH"] == ("patch", "partial_update")


@pytest.mark.django_db
class TestAnEntryAsItsRestWrite:
    def test_a_create_without_an_id_in_its_body_takes_the_entry_s(self, client):
        entry = created()
        response = pushed(client, entry)

        assert response.json()["results"][0]["status"] == 201
        assert str(SyncTask.objects.get().pk) == entry["id"]

    def test_a_create_without_a_body_is_refused_as_an_empty_create(self, client):
        entry = {"key": "k", "method": "POST", "model": "tasks", "id": TASK}

        assert set(pushed(client, entry).json()["results"][0]["errors"]) == {"owner", "title"}

    @pytest.mark.parametrize("query", ["", "?trace=1"])
    def test_its_key_replays_through_rest_as_the_same_write(self, client, query):
        support.create("tasks", {"id": TASK, "owner": ALICE, "title": "t"})
        etag = client.get(f"/api/tasks/{TASK}/")["ETag"]
        entry = {"key": str(uuid.uuid4()), "method": "PATCH", "model": "tasks", "id": TASK, "body": {"done": True}}
        client.post(f"/sync/push{query}", {"entries": [{**entry, "if_match": etag}]}, format="json", headers=HEADERS)
        create_key = str(uuid.uuid4())
        pushed(client, {**created(create_key, 2)})

        patched = client.patch(
            f"/api/tasks/{TASK}/",
            {"done": True},
            format="json",
            headers={"Idempotency-Key": entry["key"], "If-Match": etag},
        )
        made = client.post(
            "/api/tasks/",
            {"id": created(number=2)["id"], "owner": ALICE, "title": "t"},
            format="json",
            headers={"Idempotency-Key": create_key},
        )

        assert (patched.status_code, patched["Idempotent-Replayed"]) == (200, "true")
        assert (made.status_code, made["Idempotent-Replayed"]) == (201, "true")

    def test_an_entry_of_a_model_routed_nowhere_still_runs(self, monkeypatch):
        seen = []
        making = pushing.sub_request
        monkeypatch.setattr(pushing, "sub_request", lambda *args: seen.append(args[2]) or making(*args))
        client = APIClient()
        client.force_authenticate(support.user(ALICE))
        with override_settings(ROOT_URLCONF="tests.testapp.sync_urls_push_only"):
            pushed(client, created())
            pushed(client, {"key": "p", "method": "PATCH", "model": "tasks", "id": created()["id"], "body": {}})

        assert seen == ["/tasks/", f"/tasks/{created()['id']}/"]

    def test_entries_are_routed_by_the_push_s_own_urlconf(self, client, monkeypatch):
        asked = []
        enumerator = pushing.EndpointEnumerator

        def recording(urlconf=None):
            asked.append(urlconf)
            return enumerator(urlconf=urlconf)

        monkeypatch.setattr(pushing, "EndpointEnumerator", recording)
        backend = ViewPushBackend({})
        request = Request(RequestFactory().post("/sync/push"))
        request._request.urlconf = "tests.testapp.sync_urls"
        request.user, request.auth = support.user(ALICE), None
        backend.run(request, [])
        del request._request.urlconf
        backend.run(request, [])

        assert asked == ["tests.testapp.sync_urls", None]

    def test_each_entry_runs_in_a_history_context_of_its_place(self, client, monkeypatch):
        places = []
        context = pushing.entry_context

        def recording(index):
            places.append(index)
            return context(index)

        monkeypatch.setattr(pushing, "entry_context", recording)
        pushed(client, created("a", 1), created("b", 2))

        assert places == [0, 1]
