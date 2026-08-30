# history

`HistoryMixin` adds two endpoints to a `BaseModelViewSet` for a model tracked with
[`@track_events()`](../../apps/common/db/history.md), both paginated (via the viewset's own
`pagination_class`), newest first, using an auto-built serializer over the model's history (see
[`generic_history_serializer()`](../serializers/history.md)). No other configuration required -
everything is resolved from `self.model` alone.

```python
class WidgetViewSet(HistoryMixin, BaseModelViewSet):
    model = Widget
    endpoint = "widgets"
    serializer_class = WidgetSerializer

router.register(WidgetViewSet.endpoint, WidgetViewSet, basename="widget")
```

- `GET /widgets/3/history/` - one object's history, governed by the viewset's own permissions
  same as any other detail action (`history()` calls `self.get_object()` first, so
  `get_queryset()`/permission filtering applies before any events are returned).
- `GET /widgets/history/` - history across every instance of the model, restricted to
  superusers for now (`IsSuperUser`) - override `history_list_permission_classes` to change that.

```
GET /widgets/3/history/?action=update&created_after=2026-08-01T00:00:00Z
GET /widgets/history/?object_id=3&action=update

[
  {"event_id": 12, "event_created_at": "2026-08-09T10:00:00Z", "action": "update",
   "changes": {"count": [0, 5]}, "id": "...", "name": "New name", "count": 5}
]
```

- Filtering is built in on `action` (insert/update/delete), `created_after`/`created_before`
  (`pgh_created_at` range), `object_id` (which instance - redundant but harmless on the
  per-object endpoint, the main point of it on the cross-object one), and `actor` (pghistory's
  context user - only added if `pghistory.middleware.HistoryMiddleware`, or a subclass, is
  installed). `actor_id`'s value is annotated from `pgh_context` JSON unless the model was tracked
  with an `actor` [`ContextField`](../../apps/common/db/history.md#real-indexed-columns-from-context---contextfield),
  in which case that real column is used instead and the annotation is skipped.
- `extra_history_filters` is your own extension point, merged on top of the built-ins - a matching
  key overrides one, a new key just adds one. `context_filter(key)` builds a filter over a key in
  pghistory's context JSON, without needing to know the `pgh_context__<key>` field-name
  convention:

  ```python
  class OrgWidgetViewSet(HistoryMixin, BaseModelViewSet):
      ...
      extra_history_filters = {"org_id": context_filter("org_id")}
  ```

- Override `default_history_filters()` instead to replace the built-in set entirely -
  `extra_history_filters` still layers on top of whatever that returns.
- `history_filterset_class`/`history_serializer_class` are `classproperty`s cached on the class
  (same pattern as `FilterSetMixin.filterset_class`), not rebuilt on every request.
- Wired through `get_serializer_class()` (the hook drf-spectacular's `AutoSchema` already reads),
  so schema generation picks up both actions' real field types with no extra `@extend_schema`
  needed.
- Once an object is deleted, `GET <endpoint>/{pk}/history/` 404s permanently (`get_object()` can
  no longer find the row) even though the delete event itself is recorded - `GET
  <endpoint>/history/?object_id=<pk>` still reaches it, since that endpoint doesn't require the
  object to still exist.
