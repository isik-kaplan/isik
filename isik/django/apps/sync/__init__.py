"""
`isik.django.apps.sync` - offline sync's server: a log the database writes inside every change to a model
declared with `syncs()`, a feed of those changes per person (`/sync/changes`), and a push taking a client's
queued writes in batches (`/sync/push`), each through the model's own REST view.

Declare a model with `syncs()` (in `declarations`), configure with `ISIK['DJANGO']['SYNC']` (see `conf`), route
`SyncViewSet` (in `drf`), and add `add_sync_extensions` (in `spectacular`) to drf-spectacular's postprocessing
hooks. The wire protocol is isik's sync protocol, version 1 - `protocol/sync/v1` in isik-ts.

Needs `pip install isik[sync]`.
"""

from isik._internal.checks import check_extra


check_extra("sync", "rest_framework")
