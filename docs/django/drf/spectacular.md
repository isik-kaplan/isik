# spectacular

A drf-spectacular `AutoSchema` fixing the gaps that are properties of isik's own mixins, not of
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

## `PreconditionMixin`

What [`PreconditionMixin`](viewsets/preconditions.md) adds to a request and a response is read from
headers, so drf-spectacular finds none of it on its own. Documented here, so generated clients know it:

- A write that checks `If-Match` (PUT, PATCH, DELETE, a `detail=True` POST, unless exempt) has it as a
  header parameter - required unless the view's `required` option is off. Its responses gain a 412, a
  `<Name>PreconditionFailed` component of `detail`, `code` and `current`, `current` being the
  `retrieve` serializer. When `missing_row_status` is 412, it's `<Name>PreconditionFailedOrGone`, with
  `current` nullable. They also gain a 428 (the `PreconditionRequired` component, `detail` and `code`),
  unless `If-Match` is optional and `*` accepted, so that nothing answers 428.
- `retrieve` has `If-None-Match` as an optional header, and a 304 with no body - unless `not_modified`
  is off.
- The `ETag` response header is on each of their 200, 201, 304 and 412 responses, and on a create's
  201. A delete's 204 has none.

`code` is a string described as its one value rather than an enum: drf-spectacular lifts every enum
into a component named after its field, and the same `code` in every resource's 412 - or a project's
own `code` field - would collide, a warning that `--fail-on-warn` fails on.

## Robustness

Resolving a view's serializer class to check for `ConditionalSerializerMixin` can legitimately
raise - e.g. `GenericAPIView`'s own default `get_serializer_class()` asserts when `serializer_class`
was never set, expecting a subclass to resolve it dynamically per action instead. That's caught and
treated the same as "no serializer" rather than failing the whole document - matching how
drf-spectacular's own equivalent internal call already tolerates it, with a warning naming the view.
