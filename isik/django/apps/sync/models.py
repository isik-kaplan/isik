"""The sync log isik ships, `SyncLogEntry` - swapped out by `ISIK['DJANGO']['SYNC']['LOG_MODEL']` for a project
owning it, see `bases.py`."""

from isik._internal.translation import gettext_lazy
from isik.django.apps.sync.bases import AbstractSyncLogEntry


class SyncLogEntry(AbstractSyncLogEntry):
    class Meta(AbstractSyncLogEntry.Meta):
        db_table = "isik_sync_log"
        swappable = "ISIK_SYNC_LOG_MODEL"
        verbose_name = gettext_lazy("sync log entry")
        verbose_name_plural = gettext_lazy("sync log entries")
