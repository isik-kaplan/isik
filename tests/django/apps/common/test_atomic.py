"""not_atomic() - a view served outside ATOMIC_REQUESTS' transaction, saying why."""

from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory
from django.views import View

from isik.common.utils.exemptions import declared_exemptions
from isik.django.apps.common.atomic import NotAtomic, not_atomic, not_atomic_problems
from tests.django.apps.common import check_views


REASON = "streams a long export, which one transaction would hold open for minutes"


class TestOnAFunction:
    def test_leaves_the_transaction_on_the_default_database(self):
        view = not_atomic(REASON)(lambda request: HttpResponse("ok"))

        assert view._non_atomic_requests == {"default"}

    def test_on_the_database_named(self):
        view = not_atomic(REASON, using="other")(lambda request: HttpResponse("ok"))

        assert view._non_atomic_requests == {"other"}

    def test_carries_its_reason_as_a_not_atomic(self):
        view = not_atomic(REASON)(lambda request: HttpResponse("ok"))

        assert type(view.not_atomic) is NotAtomic
        assert view.not_atomic == REASON
        assert NotAtomic.rule == "isik.not-atomic"

    def test_takes_one_already_made(self):
        made = NotAtomic(reason=REASON)

        assert not_atomic(made)(lambda request: HttpResponse("ok")).not_atomic is made

    def test_a_reason_under_the_floor_is_refused_naming_where(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            not_atomic("big")

        assert str(raised.value).startswith("not_atomic.reason - NotAtomic needs a reason of at least")

    def test_is_listed_where_it_was_made(self):
        view = not_atomic(REASON)(lambda request: HttpResponse("ok"))

        made = [each for each in declared_exemptions() if each is view.not_atomic]
        # By its place in the repository - mutmut runs the tests from a copy of it.
        assert made and Path(made[0].file).parts[-5:] == Path(__file__).parts[-5:]


class TestOnAClass:
    def test_as_view_carries_both(self):
        callback = check_views.ExportView.as_view()

        assert callback._non_atomic_requests == {"default"}
        assert callback.not_atomic == REASON

    def test_still_serves(self):
        response = check_views.ExportView.as_view()(RequestFactory().get("/export/"))

        assert response.content == b"ok"

    def test_leaves_the_other_views_sharing_its_dispatch_alone(self):
        class Plain(View):
            pass

        assert not hasattr(Plain.as_view(), "_non_atomic_requests")
        assert not hasattr(View.dispatch, "not_atomic")

    def test_returns_the_class(self):
        class Export(View):
            pass

        assert not_atomic(REASON)(Export) is Export


class TestTheProblems:
    def test_one_per_view_left_silently(self):
        assert not_atomic_problems("tests.django.apps.common.check_urls") == [
            "leaves_silently (leaves-silently/) is served outside a transaction without saying why - decorate it "
            "with not_atomic(reason) instead of transaction.non_atomic_requests."
        ]

    def test_a_class_left_silently_is_named_by_its_class_once(self, settings):
        assert not_atomic_problems("tests.django.apps.common.atomic_urls") == [
            "SilentExport (export/) is served outside a transaction without saying why - decorate it with "
            "not_atomic(reason) instead of transaction.non_atomic_requests."
        ]


class TestOnAClassDefinedHere:
    """Decorated in the test itself - a class decorated where it's defined, at import, is decorated once."""

    def test_its_dispatch_hands_on_the_request_and_its_arguments(self):
        class Echo(View):
            def get(self, request, *args, **kwargs):
                return HttpResponse(f"{request.path} {args} {kwargs}")

        decorated = not_atomic(REASON)(Echo)
        response = decorated.as_view()(RequestFactory().get("/echo/"), "first", pk=3)

        assert response.content == b"/echo/ ('first',) {'pk': 3}"
        assert decorated.dispatch.not_atomic == REASON
        assert decorated.as_view()._non_atomic_requests == {"default"}
