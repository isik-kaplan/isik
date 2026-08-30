# spectacular

A drf-spectacular `AutoSchema` fixing two gaps that are properties of isik's own mixins, not of
any one consumer's viewsets - wire it in once, project-wide:

```python
REST_FRAMEWORK = {"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}
```

A project with its own `AutoSchema` customizations subclasses this instead of drf-spectacular's own.

## `HistoryMixin`

Fixes [`history()`/`history_list()`'s](viewsets/history.md) remaining schema defects:

- Both actions are now typed as returning a paginated array, not a single object -
  drf-spectacular's own action-name heuristic doesn't know that, since neither is called `list`.
- The two paths get distinct operation ids (`widgets_history`/`widgets_history_list`) instead of
  colliding - they tokenize identically once the path parameter is dropped, since that's exactly
  what makes both endpoints describable as "the same kind of thing" at two different scopes.
- The built-in filters (`action`, `created_after`, `created_before`, `object_id`, `actor`, and
  anything added via `extra_history_filters`) appear as query parameters - `history_filterset_class`
  is applied inside the action rather than exposed as `filterset_class`, so drf-spectacular's own
  `django-filter` introspection never sees it otherwise.
- The tracked model's *own* `filter_backends`-derived filters (e.g. `is_active`, `created_at__gte`
  from a `filterset_fields`/`DjangoFilterBackend` setup on the rest of the viewset) are **not**
  advertised on either history endpoint - passing them there does nothing, since neither action
  ever calls `filter_queryset()`. Typing both actions as list views (the first bullet) is what
  makes drf-spectacular consider `filter_backends` at all, so this needs its own fix rather than
  falling out of the others.

## `ConditionalSerializerMixin`

Any serializer using [`ConditionalSerializerMixin`](serializers/conditional_serializer.md) gets
`only=`/`exclude=` documented as query parameters, on every operation that uses it - they're read
straight off the query string inside `get_fields()`, so there's nothing for schema introspection to
find on its own otherwise. `include=` joins them too, but only when the serializer actually
declares `Meta.relational_fields` - its enum comes from there, so it can't drift out of sync with
what the mixin accepts, and it's omitted entirely (rather than published with no enum - effectively
free-text, accepting a value that does nothing) when there's nothing to nest.

## Robustness

Resolving a view's serializer class to check for `ConditionalSerializerMixin` can legitimately
raise - e.g. `GenericAPIView`'s own default `get_serializer_class()` asserts when `serializer_class`
was never set, expecting a subclass to resolve it dynamically per action instead. That's caught and
treated the same as "no serializer" rather than failing the whole document - matching how
drf-spectacular's own equivalent internal call already tolerates it, with a warning naming the view.
