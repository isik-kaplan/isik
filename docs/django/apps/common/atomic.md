# atomic

`not_atomic(reason, using=None)` - `transaction.non_atomic_requests`, saying why. A view served
outside `ATOMIC_REQUESTS`' transaction leaves whatever it wrote before failing written, and loses the
row locks and rollbacks isik's other guards count on (an idempotency claim, a precondition's 412), so
leaving it is an exemption like any other:

```python
from isik.django.apps.common.atomic import not_atomic


@not_atomic("streams a large export, which one transaction would hold open for minutes")
def export(request): ...


@not_atomic("streams a large export, which one transaction would hold open for minutes")
class ExportView(View): ...
```

- On a function view, on what `as_view()` returns, or on a class-based view's class - which gets a
  `dispatch` of its own, so the other views inheriting its `dispatch` stay in the transaction.
- `using` names the database, as `non_atomic_requests(using)` does.
- The reason is a `NotAtomic` exemption (rule `isik.not-atomic`), held to its floor and listed by
  `manage.py exemptions` where it was made.

The system check `isik.E011` ([checks](checks.md)) refuses a routed view served outside the
transaction - `transaction.non_atomic_requests` on it - without one. `not_atomic_problems(urlconf=None)`
lists them:

```
leaves_silently (export/) is served outside a transaction without saying why - decorate it with
not_atomic(reason) instead of transaction.non_atomic_requests.
```
