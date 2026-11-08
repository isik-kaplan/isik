"""
`ISIK`, the one setting isik reads - a dict holding every part of isik a project configures:

    ISIK = {
        "EXEMPTIONS": {"isik.django.policy.organization-is-set-up": Requirement.OPTIONAL},
        "DJANGO": {
            "CHECKS": {Check.DOCUMENTATION: {CheckOption.APPS: ["billing"]}},
            "PRIMARY_KEY": "isik.django.apps.common.db.models.uuid7",
            "SCHEMA_LANGUAGE": "en",
            "FEATURES": {"SETTINGS": "billing.feature_settings", "GRANT_MODEL": "billing.Grant"},
            "NOTIFICATIONS": {"PROJECT_NAME": "Acme"},
            "SMS": {"default": {"BACKEND": "isik.django.sms.backends.console.SMSBackend"}},
            "IDEMPOTENCY": {"CLAIM_MODEL": "idempotency_by_reference.IdempotencyClaim"},
        },
    }

Its keys follow isik's own layers: `EXEMPTIONS` is read by isik's non-Django code too, everything under `DJANGO`
by `isik.django` alone. Each area's own keys are documented with the area.

A swappable model is named here like everything else - `ISIK['DJANGO']['FEATURES']['GRANT_MODEL']` - and isik
gives Django the top-level setting it reads a swappable model by, `ISIK_FEATURES_GRANT_MODEL`, when its apps load.
"""

from django.conf import settings
from django.test.utils import override_settings


SETTING = "ISIK"

# {key: what it holds} - ISIK's own keys, and those of ISIK['DJANGO'].
KEYS = {"EXEMPTIONS": "rule levels", "DJANGO": "isik.django's areas"}
# {area: its keys} - an area of ISIK['DJANGO'] whose keys are checked here; FEATURES, NOTIFICATIONS and SMS check
# their own, and CHECKS, PRIMARY_KEY and SCHEMA_LANGUAGE are what they hold.
AREA_KEYS = {
    "HISTORY": ("CONTEXT_KEYS", "CONTEXT_VERSION", "CONTEXT_BUILD", "REQUEST_ID_HEADER", "TASK_DISPATCH_ON_COMMIT"),
    "SESSIONS": ("HEADER_NAME", "DEAD_SESSION_HEADER"),
    "IDEMPOTENCY": ("CLAIM_MODEL", "BODY_CODEC", "LOCK_TIMEOUT", "NORMALIZE"),
    "DRF": ("PAGINATION_MAX_PAGE_SIZE",),
    "FEEDBACK": ("COMMENTS_TIPTAP_SCHEMA_PATH",),
    "TEMPLATED_FIELDS": ("DELIMITERS", "UNDEFINED", "POLICY"),
}
DJANGO_AREAS = ("CHECKS", "PRIMARY_KEY", "SCHEMA_LANGUAGE", "FEATURES", "NOTIFICATIONS", "SMS", *AREA_KEYS)


def configured():
    """`ISIK` as the project set it - `{}` when it set none."""
    value = getattr(settings, SETTING, None)
    return value if isinstance(value, dict) else {}


def django_area(name, default=None):
    """`ISIK['DJANGO'][name]`, else `default`."""
    django = configured().get("DJANGO")
    return django.get(name, default) if isinstance(django, dict) else default


def django_setting(area, key, default=None):
    """`ISIK['DJANGO'][area][key]`, else `default`."""
    values = django_area(area)
    return values.get(key, default) if isinstance(values, dict) else default


def _merged(current, changes, depth):
    if not depth or not isinstance(current, dict) or not isinstance(changes, dict):
        return changes
    return {**current, **{key: _merged(current.get(key), value, depth - 1) for key, value in changes.items()}}


def isik_with(**changes):
    """
    `ISIK` with `changes` made - each of an area's keys given replacing the project's, every other key kept:

        isik_with(DJANGO={"NOTIFICATIONS": {"MAX_ATTEMPTS": 1}})   # every other notifications key kept
        isik_with(EXEMPTIONS={"isik.django.precondition": Requirement.OFF})
    """
    return {
        **configured(),
        **{key: _merged(configured().get(key), value, 2) for key, value in changes.items()},
    }


class override_isik(override_settings):  # noqa: N801 - named like the override_settings it is
    """
    `override_settings` for `ISIK`, making `changes` the way `isik_with()` does - as a decorator or a context
    manager, over what `ISIK` is when it's entered:

        @override_isik(DJANGO={"NOTIFICATIONS": {"MAX_ATTEMPTS": 1}})
        def test_gives_up_after_one_attempt(): ...
    """

    def __init__(self, **changes):
        self.changes = changes
        super().__init__()

    def enable(self):
        self.options = {SETTING: isik_with(**self.changes)}
        super().enable()

    def decorate_class(self, cls):
        self.options = {SETTING: isik_with(**self.changes)}
        return super().decorate_class(cls)


class _Areas:
    def __init__(self, target):
        object.__setattr__(self, "_target", target)

    def _isik(self):
        value = getattr(self._target, SETTING, None)
        return dict(value) if isinstance(value, dict) else {}

    def _where(self, name):
        if name in KEYS and name != "DJANGO":
            return None
        if name in DJANGO_AREAS:
            return "DJANGO"
        raise AttributeError(name)

    def __getattr__(self, name):
        isik = self._isik()
        where = self._where(name)
        holder = isik if where is None else isik.get(where) or {}
        if name not in holder:
            raise AttributeError(name)
        return holder[name]

    def __setattr__(self, name, value):
        self._put(name, lambda holder: {**holder, name: value})

    def __delattr__(self, name):
        self._put(name, lambda holder: {key: held for key, held in holder.items() if key != name})

    def _put(self, name, change):
        isik = self._isik()
        where = self._where(name)
        if where is None:
            isik = change(isik)
        else:
            isik[where] = change(dict(isik.get(where) or {}))
        setattr(self._target, SETTING, isik)


def isik_areas(target):
    """
    Each of `ISIK`'s areas as an attribute of `target` - pytest-django's `settings` fixture, or anything else
    holding settings - read, set or deleted whole, each set writing `target.ISIK` again:

        def test_gives_up_after_one_attempt(settings):
            isik_areas(settings).NOTIFICATIONS = {"MAX_ATTEMPTS": 1}   # the whole area, nothing else kept

    `EXEMPTIONS` is `ISIK['EXEMPTIONS']`, and every area of isik.django's - `NOTIFICATIONS` - `ISIK['DJANGO']`'s.
    """
    return _Areas(target)


# {top-level setting: (area, key)} - every swappable model setting an app gave from ISIK, for the check
# holding the two the same.
SWAPPABLE_SETTINGS = {}


def give_swappable_settings(area, names):
    """
    Gives Django each top-level swappable setting in `names` that `ISIK['DJANGO'][area]` names a model for,
    and the project didn't set itself - `ISIK_FEATURES_GRANT_MODEL` from `ISIK['DJANGO']['FEATURES']['GRANT_MODEL']`.
    Called as an app's config module loads, before any model or migration reads one.
    """
    given = django_area(area) or {}
    for name in names:
        key = name.removeprefix(f"ISIK_{area}_")
        SWAPPABLE_SETTINGS[name] = (area, key)
        if key in given and not hasattr(settings, name):
            setattr(settings, name, given[key])


def swappable_keys(names, area):
    """The keys of `ISIK['DJANGO'][area]` naming the swappable models `names` are the top-level settings of."""
    return {name.removeprefix(f"ISIK_{area}_") for name in names}
