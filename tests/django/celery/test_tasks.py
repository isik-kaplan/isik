import datetime
import decimal
import uuid

import amqp
import pghistory
import pytest
from celery import Celery, Task
from django.core.serializers.json import DjangoJSONEncoder
from django.test import RequestFactory

from isik.django.apps.common.db.history import open_history_context
from isik.django.apps.common.middleware import HistoryContextMiddleware
from isik.django.celery import HistoryContextTask
from tests.testapp.models import EmailUser, Widget, WidgetEvent


app = Celery("isik-tests", set_as_current=False)
app.conf.task_always_eager = True


@app.task(base=HistoryContextTask, name="widgets.make")
def make_widget(name):
    return Widget.objects.create(name=name).pk


@app.task(base=HistoryContextTask, name="widgets.echo")
def echo(*args, **kwargs):
    return list(args), kwargs


@app.task(base=HistoryContextTask, name="widgets.peek")
def peek():
    return open_history_context()


class TenantTask(HistoryContextTask):
    carried_history_keys = ("user", "schema", "organization")

    def worker_history_context(self):
        return {**super().worker_history_context(), "version": "1.2.3"}


@app.task(base=TenantTask, name="widgets.make_for_tenant")
def make_widget_for_tenant(name):
    return Widget.objects.create(name=name).pk


@pytest.fixture
def sent(monkeypatch):
    """The options each dispatch hands Celery, instead of sending anything."""
    options = []

    def apply_async(self, args=None, kwargs=None, **kw):
        options.append({"args": args, "kwargs": kwargs, **kw})
        return "sent"

    monkeypatch.setattr(Task, "apply_async", apply_async)
    return options


def while_serving(dispatch, context=None):
    """Runs `dispatch` as the view of a request served through HistoryContextMiddleware."""

    class Middleware(HistoryContextMiddleware):
        def get_context(self, request):
            return {**super().get_context(request), **(context or {})}

    return Middleware(get_response=lambda request: dispatch())(RequestFactory().post("/widgets/"))


def context_of(widget_pk):
    return WidgetEvent.objects.get(pgh_obj_id=widget_pk).pgh_context.metadata


def test_a_task_dispatched_while_serving_a_request_carries_its_user(sent):
    while_serving(lambda: make_widget.delay("bolt"), {"user": 7})

    # Not the url: the task isn't that route.
    assert sent[0]["headers"] == {"isik_history_context": {"user": 7, "caused_by": "request"}}


def test_a_task_dispatched_outside_a_request_is_the_systems(sent):
    make_widget.delay("bolt")

    assert sent[0]["headers"] == {"isik_history_context": {"caused_by": "system"}}


def test_the_carried_keys_are_the_tasks_to_choose(sent):
    while_serving(lambda: make_widget_for_tenant.delay("bolt"), {"user": 7, "schema": "acme", "domain": "acme.test"})

    # organization is carried but absent, so skipped rather than sent as None; domain isn't carried.
    assert sent[0]["headers"]["isik_history_context"] == {"user": 7, "schema": "acme", "caused_by": "request"}


def test_a_callers_own_headers_are_kept(sent):
    make_widget.apply_async(("bolt",), headers={"trace": "abc"})

    assert sent[0]["headers"] == {"isik_history_context": {"caused_by": "system"}, "trace": "abc"}


def test_a_caller_setting_the_header_itself_wins(sent):
    make_widget.apply_async(("bolt",), headers={"isik_history_context": {"user": 9, "caused_by": "request"}})

    assert sent[0]["headers"] == {"isik_history_context": {"user": 9, "caused_by": "request"}}


def test_the_arguments_options_and_result_are_passed_through(sent):
    assert echo.apply_async((1,), {"flag": True}, countdown=30) == "sent"

    assert (sent[0]["args"], sent[0]["kwargs"], sent[0]["countdown"]) == ((1,), {"flag": True}, 30)


def test_the_worker_hands_the_body_its_arguments():
    assert echo.apply((1,), {"flag": True}).get() == ([1], {"flag": True})


def test_a_task_run_inside_a_request_is_handed_its_arguments():
    assert while_serving(lambda: echo.delay(1, flag=True).get()) == ([1], {"flag": True})


@pytest.mark.django_db
def test_the_worker_writes_inside_the_cause_it_was_sent():
    user = EmailUser.objects.create(username="alice", email="alice@example.com")
    cause = {"user": user.pk, "caused_by": "request"}

    widget_pk = make_widget.apply(("bolt",), headers={"isik_history_context": cause}).get()

    assert context_of(widget_pk) == {"user": user.pk, "caused_by": "request", "task": "widgets.make"}


@pytest.mark.django_db
def test_a_message_without_the_header_runs_as_the_systems():
    widget_pk = make_widget.apply(("bolt",)).get()

    assert context_of(widget_pk) == {"caused_by": "system", "task": "widgets.make"}


@pytest.mark.django_db
def test_the_worker_adds_its_own_keys():
    widget_pk = make_widget_for_tenant.apply(("bolt",)).get()

    assert context_of(widget_pk) == {"caused_by": "system", "task": "widgets.make_for_tenant", "version": "1.2.3"}


@pytest.mark.django_db
def test_the_worker_context_wins_over_a_cause_claiming_the_same_key():
    cause = {"task": "somebody.else", "caused_by": "request"}

    widget_pk = make_widget.apply(("bolt",), headers={"isik_history_context": cause}).get()

    assert context_of(widget_pk)["task"] == "widgets.make"


@pytest.mark.django_db
def test_from_a_request_to_the_rows_the_worker_writes(sent):
    # Both halves together: what dispatch puts on the message is what the worker's rows carry.
    user = EmailUser.objects.create(username="alice", email="alice@example.com")
    while_serving(lambda: make_widget.delay("bolt"), {"user": user.pk})

    widget_pk = make_widget.apply(("bolt",), headers=sent[0]["headers"]).get()

    assert context_of(widget_pk) == {"user": user.pk, "caused_by": "request", "task": "widgets.make"}


def test_a_task_run_inside_a_request_leaves_its_context_alone():
    # task_always_eager, or the task called directly: pghistory would fold a context opened here into
    # the request's for good, and every later row of the request would claim to be the task's.
    def view():
        during = peek.delay().get()
        return during, open_history_context()

    during, after = while_serving(view, {"user": 7})

    assert during == after == {"user": 7, "url": "/widgets/"}


def test_a_task_run_inside_some_other_context_opens_its_own_keys_in_it():
    # Outside a request the reader can't see a surrounding pghistory.context() - the task's keys join
    # that one, which is what pghistory does with any nested context.
    with pghistory.context(command="import") as opened:
        peek()

    assert opened.metadata == {"command": "import", "caused_by": "system", "task": "widgets.peek"}


class Recorder(HistoryContextTask):
    def history_cause(self):
        return {"user": uuid.UUID(int=7), "amount": decimal.Decimal("1.50"), "at": FROZEN, "caused_by": "request"}


@app.task(base=Recorder, name="widgets.record")
def record():
    return None


FROZEN = datetime.datetime(2026, 10, 3, 12, 30, tzinfo=datetime.UTC)


def test_the_cause_travels_as_pghistory_would_store_it(sent):
    record.delay()

    assert sent[0]["headers"]["isik_history_context"] == {
        "user": "00000000-0000-0000-0000-000000000007",
        "amount": "1.50",
        "at": "2026-10-03T12:30:00Z",
        "caused_by": "request",
    }


def test_the_cause_fits_in_a_real_amqp_frame(sent):
    # What hid this: eager tasks and stubbed sends never encode a frame, and AMQP's table encoder is
    # the one that refuses a UUID. This is the encoder a real broker connection runs headers through.
    record.delay()

    frame = amqp.serialization.dumps("F", [sent[0]["headers"]])
    (decoded,), _ = amqp.serialization.loads("F", frame, 0)

    assert decoded == sent[0]["headers"]


def test_a_raw_uuid_is_what_amqp_refuses():
    with pytest.raises(amqp.exceptions.FrameSyntaxError):
        amqp.serialization.dumps("F", [{"isik_history_context": {"user": uuid.UUID(int=7)}}])


def test_the_encoding_follows_pghistorys_own_setting(sent, settings):
    settings.PGHISTORY_JSON_ENCODER = "tests.django.celery.test_tasks.ShoutingEncoder"

    record.delay()

    assert sent[0]["headers"]["isik_history_context"]["user"] == "UUID:00000000-0000-0000-0000-000000000007"


class OnlyTheCauseTravels(Recorder):
    def header_safe(self, cause):
        return {"caused_by": cause["caused_by"]}


@app.task(base=OnlyTheCauseTravels, name="widgets.record_plainly")
def record_plainly():
    return None


def test_a_task_can_say_how_its_values_travel(sent):
    record_plainly.delay()

    assert sent[0]["headers"] == {"isik_history_context": {"caused_by": "request"}}


class ShoutingEncoder(DjangoJSONEncoder):
    def default(self, o):
        if isinstance(o, uuid.UUID):
            return f"UUID:{o}"
        return super().default(o)
