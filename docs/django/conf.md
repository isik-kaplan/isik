# ISIK - the one setting

`isik.django.conf`. Everything a project configures about isik is in one Django setting, `ISIK`, a dict
whose keys follow isik's own layers:

```python
from isik.common.utils.exemptions import Requirement
from isik.django.apps.common.checks import Check, CheckOption

ISIK = {
    # Read by isik's non-Django code too.
    "EXEMPTIONS": {"isik.django.policy.organization-is-set-up": Requirement.OPTIONAL},
    # Read by isik.django alone.
    "DJANGO": {
        "CHECKS": {Check.DOCUMENTATION: {CheckOption.APPS: ["billing"]}},
        "PRIMARY_KEY": "isik.django.apps.common.db.models.uuid7",
        "SCHEMA_LANGUAGE": "en",
        "FEATURES": {"SETTINGS": "billing.feature_settings", "GRANT_MODEL": "billing.Grant"},
        "NOTIFICATIONS": {"PROJECT_NAME": "Acme"},
        "SMS": {"default": {"BACKEND": "isik.django.sms.backends.console.SMSBackend"}},
    },
}
```

| key | holds | documented in |
|---|---|---|
| `EXEMPTIONS` | each rule's `Requirement` | [exemptions](../common/utils/exemptions.md) |
| `DJANGO.CHECKS` | each system check's options | [checks](apps/common/checks.md) |
| `DJANGO.PRIMARY_KEY` | the function every key comes from | [primary keys](../primary-keys.md) |
| `DJANGO.SCHEMA_LANGUAGE` | the language the API schema is generated in | [spectacular](drf/spectacular.md) |
| `DJANGO.FEATURES` | the features app | [features](apps/features/README.md) |
| `DJANGO.NOTIFICATIONS` | the notifications apps | [notifications](apps/notifications/README.md) |
| `DJANGO.SMS` | the SMS senders | [sms](sms.md) |
| `DJANGO.HISTORY` | `CONTEXT_KEYS`, `CONTEXT_VERSION`, `CONTEXT_BUILD`, `REQUEST_ID_HEADER`, `TASK_DISPATCH_ON_COMMIT` | [history middleware](apps/common/middleware/history.md), [celery](celery/README.md) |
| `DJANGO.SESSIONS` | `HEADER_NAME`, `DEAD_SESSION_HEADER` | [session middleware](apps/common/middleware/session.md) |
| `DJANGO.IDEMPOTENCY` | `CLAIM_MODEL`, `BODY_CODEC`, `LOCK_TIMEOUT`, `NORMALIZE` | [idempotency](apps/idempotency/README.md) |
| `DJANGO.DRF` | `PAGINATION_MAX_PAGE_SIZE` | [pagination](drf/pagination.md) |
| `DJANGO.FEEDBACK` | `COMMENTS_TIPTAP_SCHEMA_PATH` | [tiptap](apps/feedback/comments/tiptap.md) |
| `DJANGO.TEMPLATED_FIELDS` | `DELIMITERS`, `UNDEFINED`, `POLICY` | [templated fields](apps/templated_fields/README.md) |

A key isik doesn't read is an error, `isik.E022`, naming the key it's closest to - in `ISIK`, in `DJANGO`, and
in each area whose keys are listed above.

Only isik's own settings are here. A setting isik reads that belongs to Django or another library stays where
that library reads it: `AUTH_USER_MODEL`, the `SESSION_COOKIE_*` settings, pghistory's `PGHISTORY_APPEND_ONLY`,
DRF's `REST_FRAMEWORK`.

## Swappable models

A model a project owns is named in its area like everything else - `"GRANT_MODEL"` in `FEATURES`,
`"NOTICE_MODEL"` in `NOTIFICATIONS`. Django swaps a model by a top-level setting, which a migration naming the
model reads by name too - `settings.ISIK_FEATURES_GRANT_MODEL` - so isik gives Django each one from `ISIK` when
its apps load: `ISIK_` and the area, then the key. A project setting one itself is held to naming the model
`ISIK` names (`isik.E023`).

## In tests

`override_isik()` is `override_settings` for `ISIK`: an area's keys given replace the project's, and every
other key - in that area, and in every other - is kept. It works as a decorator or a context manager, over
`ISIK` as it is when it's entered:

```python
from isik.django.conf import override_isik

@override_isik(DJANGO={"NOTIFICATIONS": {"MAX_ATTEMPTS": 1}})
def test_gives_up_after_one_attempt(): ...

with override_isik(EXEMPTIONS={"isik.django.precondition": Requirement.OFF}):
    ...
```

`isik_with(**changes)` returns the dict `override_isik()` sets. `isik_areas(settings)` gives each area as an
attribute of pytest-django's `settings` fixture - read, set or deleted whole, the rest of `ISIK` kept:

```python
from isik.django.conf import isik_areas

def test_signs_with_the_project_name(settings):
    isik_areas(settings).NOTIFICATIONS = {"PROJECT_NAME": "Acme"}   # the whole area, nothing else in it kept
```

Leaving an area's `*_MODEL` keys out this way is harmless: a swappable model is read once, when the apps load,
and stays swapped for the run - which is also why naming another in a test swaps nothing.

## Reading it

`configured()` is `ISIK` as set (`{}` when it isn't), and `django_area(name, default=None)` one of the areas
under `DJANGO`.
