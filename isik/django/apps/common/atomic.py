"""not_atomic() - see its own docstring."""

import functools

from django.db import transaction

from isik._internal.reasons import made_exemption
from isik.common.utils.exemptions import Exemption


class NotAtomic(
    Exemption,
    rule="isik.not-atomic",
    changes_behavior=True,
    why=(
        "A request served outside a transaction leaves whatever it wrote before failing written, and "
        "loses the row locks and rollbacks every other guard here counts on."
    ),
):
    """Why a view is served outside `ATOMIC_REQUESTS`' transaction - `not_atomic()`'s reason."""


def not_atomic(reason, using=None):
    """
    `transaction.non_atomic_requests`, saying why - on a function view, on what `as_view()` returns, or
    on a class-based view's class:

        @not_atomic("streams a large export, which one transaction would hold open for minutes")
        def export(request): ...

    The reason is a `NotAtomic` (rule `isik.not-atomic`), held to its floor and listed by
    `manage.py exemptions`. The system check `isik.E011` refuses a routed view served outside the
    transaction without one - see `not_atomic_problems()`.
    """
    made = made_exemption("not_atomic", "reason", reason, NotAtomic)
    leave = transaction.non_atomic_requests(using)

    def mark(view):
        if isinstance(view, type):
            # A wrapper of its own: Django copies `dispatch`'s attributes onto what `as_view()` returns,
            # and the class's `dispatch` is shared with every other view inheriting it.
            dispatch = view.dispatch

            @functools.wraps(dispatch)
            def own_dispatch(self, *args, **kwargs):
                return dispatch(self, *args, **kwargs)

            view.dispatch = mark(own_dispatch)
            return view
        view = leave(view)
        view.not_atomic = made
        return view

    return mark


def not_atomic_problems(urlconf=None):
    """
    Each routed view served outside the transaction (`transaction.non_atomic_requests`) without saying
    why with `not_atomic()`, one sentence each - every urlconf the project serves by default. The
    system check `isik.E011` reports them.
    """
    from isik._internal.translation import gettext as _
    from isik.django.drf.coverage import routed_views

    problems = []
    for routed in routed_views(urlconf):
        callback = routed.callback
        if getattr(callback, "_non_atomic_requests", None) and getattr(callback, "not_atomic", None) is None:
            problems.append(
                _(
                    "%(view)s (%(route)s) is served outside a transaction without saying why - decorate it with "
                    "not_atomic(reason) instead of transaction.non_atomic_requests."
                )
                % {"view": routed.view.__qualname__, "route": routed.route}
            )
    return list(dict.fromkeys(problems))
