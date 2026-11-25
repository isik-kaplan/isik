"""
The names isik puts where someone outside the project's code sees them - an HTTP header on the wire - each a setting
of `ISIK['DJANGO']` with a default the project may set its own value over. The code using one reads it here, so this
is every one of them:

    from isik.django.public_names import public_names

    for name in public_names():
        print(name.setting, name.value(), name.overridden())

`manage.py public_names` and `isik check public_names` list them, saying which the project left at isik's default.
"""

import json
from dataclasses import dataclass

from django.apps import apps
from django.conf import settings
from django.utils.module_loading import import_string

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy
from isik.common.utils.exemption_listing import columns
from isik.django.conf import django_area, django_setting


@dataclass(frozen=True)
class PublicName:
    """One name isik exposes: the `ISIK['DJANGO'][area][key]` setting naming it, its default, and where it's seen."""

    area: str
    key: str
    default: str
    seen_as: str
    # f() -> whether the project uses what the name belongs to - an app installed, a middleware in MIDDLEWARE.
    in_use: object

    @property
    def setting(self):
        return f"ISIK['DJANGO']['{self.area}']['{self.key}']"

    def overridden(self):
        """Whether the project set it - to any value, isik's default included."""
        area = django_area(self.area)
        return isinstance(area, dict) and self.key in area

    def value(self):
        """The name in use: the project's, else the default."""
        return django_setting(self.area, self.key, self.default)


def _middleware_installed(path):
    def installed():
        wanted = import_string(path)
        return any(issubclass(import_string(each), wanted) for each in settings.MIDDLEWARE)

    return installed


def _app_installed(name):
    return lambda: apps.is_installed(name)


SYNC_PROTOCOL_HEADER = PublicName(
    "SYNC",
    "PROTOCOL_HEADER",
    "Sync-Protocol",
    gettext_lazy("the header every sync request and response names its protocol version in"),
    _app_installed("isik.django.apps.sync"),
)
REQUEST_ID_HEADER = PublicName(
    "HISTORY",
    "REQUEST_ID_HEADER",
    "X-Request-ID",
    gettext_lazy("the header a request's correlation id is read from, and answered under"),
    _middleware_installed("isik.django.apps.common.middleware.history.HistoryContextMiddleware"),
)
DEAD_SESSION_HEADER = PublicName(
    "SESSIONS",
    "DEAD_SESSION_HEADER",
    "Session-Expired",
    gettext_lazy("the header saying a request's session cookie is dead"),
    _middleware_installed("isik.django.apps.common.middleware.session.DeadSessionCookieMiddleware"),
)

PUBLIC_NAMES = (SYNC_PROTOCOL_HEADER, REQUEST_ID_HEADER, DEAD_SESSION_HEADER)


def public_names(*, in_use_only=True):
    """Every public name - those of what the project uses, unless `in_use_only` is False."""
    return [name for name in PUBLIC_NAMES if not in_use_only or name.in_use()]


def public_names_listing(names, format):
    """`names` - each setting, the name in use, whether it's isik's default or the project's, and where it's seen."""
    if format == "json":
        payload = [
            {
                "setting": name.setting,
                "value": name.value(),
                "default": name.default,
                "overridden": name.overridden(),
                "seen_as": str(name.seen_as),
            }
            for name in names
        ]
        return json.dumps({"public_names": payload}, indent=2)
    rows = [
        (name.setting, name.value(), _("set") if name.overridden() else _("isik's default"), str(name.seen_as))
        for name in names
    ]
    return "\n".join(columns(rows)) if rows else ""
