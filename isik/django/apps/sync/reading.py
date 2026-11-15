"""
Reading a synced model's rows as its REST API shows them - `Reader`, one per model and request, over the model's
own viewset: its `get_queryset()`, its permissions and its `retrieve` serializer, so the feed and the API never
disagree about a row.
"""

from django.utils.module_loading import import_string
from rest_framework.exceptions import NotAuthenticated, PermissionDenied

from isik.django.apps.common.db.concurrency import concurrency_etag, with_concurrency_etags
from isik.django.drf.viewsets.registry import ViewSetRegistryMixin


def viewset_of(synced):
    """The viewset serving a synced model: the one `syncs()` named, else the one registered for the model."""
    if synced.viewset is None:
        return ViewSetRegistryMixin.get_for_model(synced.model)
    return import_string(synced.viewset) if isinstance(synced.viewset, str) else synced.viewset


def view_for(viewset, request, action):
    """An instance of `viewset` serving `request`'s `action`, as `as_view()` would set it up."""
    return viewset(request=request, format_kwarg=None, action=action)


def upsert(synced, view, instance):
    """The feed's change writing `instance`: its REST representation, and the `ETag` REST sends for it."""
    return {
        "op": "upsert",
        "model": synced.name,
        "id": str(instance.pk),
        "etag": concurrency_etag(instance),
        "data": dict(view.get_serializer(instance).data),
    }


class Reader:
    """A synced model's rows, read for one request through the model's viewset - only those its person may see."""

    def __init__(self, synced, request, scopes):
        self.synced = synced
        self.view = view_for(viewset_of(synced), request, "retrieve")
        self.request = request
        # The ids of the scopes of the model's kind the person is in.
        prefix = f"{synced.kind}:"
        self.scope_ids = [scope.removeprefix(prefix) for scope in scopes if scope.startswith(prefix)]

    def _allowed(self):
        try:
            self.view.check_permissions(self.request)
        except (PermissionDenied, NotAuthenticated):
            return False
        return True

    def _shown(self, instance):
        try:
            self.view.check_object_permissions(self.request, instance)
        except (PermissionDenied, NotAuthenticated):
            return False
        return True

    def queryset(self, scope_ids):
        """The rows of `scope_ids` the person may see, each with its ETag."""
        attname = self.synced.scope_field.attname
        return with_concurrency_etags(self.view.get_queryset().filter(**{f"{attname}__in": scope_ids}))

    def changes(self, ids):
        """{id: upsert} for each of `ids` the person may see now."""
        if not self._allowed():
            return {}
        rows = self.queryset(self.scope_ids).filter(pk__in=ids)
        return {str(row.pk): upsert(self.synced, self.view, row) for row in rows if self._shown(row)}

    def page(self, after, count, scope_ids):
        """
        The upserts of up to `count` rows of `scope_ids` the person may see - every one when it's None - by primary
        key, after the key `after` (None: from the first), and whether another follows them.
        """
        if not self._allowed():
            return [], False
        rows = self.queryset(scope_ids).order_by("pk")
        found = []
        for row in (rows if after is None else rows.filter(pk__gt=after)).iterator():
            if not self._shown(row):
                continue
            if len(found) == count:
                return found, True
            found.append(upsert(self.synced, self.view, row))
        return found, False
