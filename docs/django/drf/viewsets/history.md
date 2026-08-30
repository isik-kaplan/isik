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
  context user). `actor_id`'s *value* is annotated from `pgh_context` JSON unless the model was
  tracked with an `actor` [`ContextField`](../../apps/common/db/history.md#real-indexed-columns-from-context---contextfield),
  in which case that real column is used instead and the annotation is skipped - and the `actor`
  *filter* follows the same precedent: it filters on that real column via `context_field_filter()`
  when an `actor` `ContextField` exists (no `HistoryMiddleware` needed - a `ContextField` is
  stamped by `pghistory.context()` directly), else on `pgh_context` JSON via `context_filter()` if
  `HistoryMiddleware` (or a subclass) is installed, else it's omitted entirely. A value that fails
  a filter's own validation (e.g. `?created_after=not-a-date`) raises a 400 naming it rather than
  silently filtering on whatever else was valid - both `context_filter()`/`context_field_filter()`
  default to an integer-typed filter, so a project whose actor pks aren't integers (e.g. UUID)
  needs its own `filter_cls` (see either function's own docstring) or that filter alone raises on
  every value.
- `extra_history_filters` is your own extension point, merged on top of the built-ins - a matching
  key overrides one, a new key just adds one. `context_filter(key)` builds a filter over a key in
  pghistory's context JSON, without needing to know the `pgh_context__<key>` field-name
  convention; `context_field_filter(event_model, name)` does the same for a `ContextField`'s own
  real column instead (needs `event_model_for(cls.model)` - pghistory's aggregate `Events` model
  has no way to reference a single tracked model's real column directly, not even via
  `pghistory.ProxyField`, so this resolves matching rows against the concrete event model first):

  ```python
  class OrgWidgetViewSet(HistoryMixin, BaseModelViewSet):
      ...
      extra_history_filters = {"org_id": context_filter("org_id")}
      # Or, if "org_id" is a ContextField on Widget's event model:
      # extra_history_filters = {"org_id": context_field_filter(event_model_for(Widget), "org_id")}
  ```

- Override `default_history_filters()` instead to replace the built-in set entirely -
  `extra_history_filters` still layers on top of whatever that returns.
- `history_withhold = [...]` names tracked fields to keep out of both endpoints' output entirely -
  forwarded straight into `generic_history_serializer(cls.model, withhold=cls.history_withhold)`,
  see its own docstring for what that does to `changes`.
- `history_list_scoped_to_queryset = True` restricts `GET <endpoint>/history/` to events for
  objects `self.get_queryset()` would return, instead of every instance of the model regardless of
  scope (the default, unaffected unless you opt in). Turn it on when a viewset's `get_queryset()`
  is itself the security boundary (e.g. scoped to the caller's own organization) rather than
  `history_list_permission_classes` alone - without it, the cross-object endpoint answers for
  objects the per-object one would 404 on.
- `history_filterset_class`/`history_serializer_class` are `classproperty`s cached on the class
  (same pattern as `FilterSetMixin.filterset_class`), not rebuilt on every request.
- Wired through `get_serializer_class()` (the hook drf-spectacular's `AutoSchema` already reads),
  so schema generation picks up both actions' real field types with no extra `@extend_schema`
  needed.
- Once an object is deleted, `GET <endpoint>/{pk}/history/` 404s permanently (`get_object()` can
  no longer find the row) even though the delete event itself is recorded - `GET
  <endpoint>/history/?object_id=<pk>` still reaches it, since that endpoint doesn't require the
  object to still exist.
- `history()`'s own lookup works with a custom `lookup_field`/`lookup_url_kwarg` - it reads the
  object through `self.get_object()`, not a hardcoded `pk`.
- `history_list_permission_classes` is enforced from `check_permissions()`, not `get_permissions()`
  - deliberately, so a subclass overriding `get_permissions()` wholesale for unrelated reasons
  (a far more commonly overridden hook) can't silently drop this enforcement the way it would if
  it lived there.
