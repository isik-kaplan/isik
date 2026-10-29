"""
`ISIK_FEATURES`, the one setting the features app reads - a dict whose keys each have a default. A hook is a
callable or its dotted path. Keys this module doesn't know are refused by a system check.
"""

from importlib import import_module

from django.conf import settings
from django.utils.module_loading import import_string


DEFAULTS = {
    # The module declaring the project's settings (declare_setting()) - imported before the overrides model is
    # built, so its columns never depend on import order.
    "SETTINGS": None,
    # f(request) -> the holder a request is about: request.user when the holder is the user model.
    "HOLDER": None,
    # A context manager entered around every read and write of grants and overrides - where those rows live
    # (a platform schema, say). None enters nothing.
    "IN_HOLDER_SCHEMA": None,
    # The sources a grant may name - a subscription, a trial, a hand-made grant.
    "GRANT_SOURCES": ("manual",),
    # Whether the features view shows a grant's source and reference to the holder.
    "SHOW_GRANT_SOURCE": False,
    # f(request, holder) -> the feature / setting names the views show that holder; None shows every one
    # declared visible_to_holder.
    "FEATURES_VISIBLE": None,
    "SETTINGS_VISIBLE": None,
    # f(request, holder, name) -> whether the request may change the holder's own value of the setting `name`.
    # None: the holder a request is about, when it's the requester.
    "MAY_CHANGE_SETTING": None,
    # f(holder) -> the people a notice about the holder reaches. None: the holder, when it's a user.
    "PEOPLE": None,
}

HOOKS = ("HOLDER", "IN_HOLDER_SCHEMA", "FEATURES_VISIBLE", "SETTINGS_VISIBLE", "MAY_CHANGE_SETTING", "PEOPLE")


def configured():
    """`ISIK_FEATURES` as the project set it."""
    return getattr(settings, "ISIK_FEATURES", None) or {}


def import_settings_module():
    """Imports `SETTINGS`, the module declaring the project's settings - where it's named."""
    path = setting("SETTINGS")
    if path:
        import_module(path)


def setting(name):
    """`name`'s value: the project's, else its default. A hook given by its dotted path is imported."""
    value = configured().get(name, DEFAULTS[name])
    if name in HOOKS and isinstance(value, str):
        return import_string(value)
    return value
