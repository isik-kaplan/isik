"""
The sync log's swappable setting, and the getter reading it - `get_sync_log_model()`. The setting names an
`"app_label.Model"`; unset, it names the model isik ships. A project owning the log subclasses
`AbstractSyncLogEntry` (`bases.py`) and names the subclass in `ISIK['DJANGO']['SYNC']['LOG_MODEL']`.
"""

from django.apps import apps
from django.conf import settings

from isik.django.conf import give_swappable_settings


LOG_MODEL = "ISIK_SYNC_LOG_MODEL"

DEFAULTS = {LOG_MODEL: "isik_sync.SyncLogEntry"}


def set_defaults():
    """Gives the swappable setting what `ISIK['DJANGO']['SYNC']` names, else its default."""
    give_swappable_settings("SYNC", DEFAULTS)
    for name, default in DEFAULTS.items():
        if not hasattr(settings, name):
            setattr(settings, name, default)


def get_sync_log_model():
    """The sync log model: `ISIK_SYNC_LOG_MODEL`'s, else isik's."""
    return apps.get_model(getattr(settings, LOG_MODEL))
