# spectacular

A drf-spectacular `AutoSchema` fixing two gaps that are properties of isik's own mixins, not of
any one consumer's viewsets - wire it in once, project-wide:

```python
REST_FRAMEWORK = {"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}
```

A project with its own `AutoSchema` customizations subclasses this instead of drf-spectacular's own.

## `HistoryMixin`

Fixes all three of [`history()`/`history_list()`'s](viewsets/history.md) remaining schema defects:

- Both actions are now typed as returning a paginated array, not a single object -
  drf-spectacular's own action-name heuristic doesn't know that, since neither is called `list`.
- The two paths get distinct operation ids (`widgets_history`/`widgets_history_list`) instead of
  colliding - they tokenize identically once the path parameter is dropped, since that's exactly
  what makes both endpoints describable as "the same kind of thing" at two different scopes.
- The built-in filters (`action`, `created_after`, `created_before`, `object_id`, `actor`, and
  anything added via `extra_history_filters`) appear as query parameters - `history_filterset_class`
  is applied inside the action rather than exposed as `filterset_class`, so drf-spectacular's own
  `django-filter` introspection never sees it otherwise.

## `ConditionalSerializerMixin`

Any serializer using [`ConditionalSerializerMixin`](serializers/conditional_serializer.md) gets
`only=`/`exclude=`/`include=` documented as query parameters, on every operation that uses it -
they're read straight off the query string inside `get_fields()`, so there's nothing for schema
introspection to find on its own otherwise. `include=`'s enum comes from `Meta.relational_fields`,
so it can't drift out of sync with what the mixin actually accepts.
