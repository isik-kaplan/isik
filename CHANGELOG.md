# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.2] - 2026-08-30

### Added

- `generic_history_serializer(model, withhold=[...])` (`isik.django.drf.serializers`) - keeps a
  named tracked field out of the flattened output entirely, while `changes` still records that it
  changed at that event with its `[old, new]` pair nulled instead of the field's real values.
  Different from `track_events(exclude=[...])`, which drops a field from the event table itself -
  this is about API exposure, not retention. Explicit, one field name at a time; isik never
  guesses at what "looks sensitive". `HistoryMixin.history_withhold` forwards into it.
- `HistoryMixin.history_list_scoped_to_queryset` (`isik.django.drf.viewsets`, default `False`) -
  restricts `GET <endpoint>/history/` to events for objects `self.get_queryset()` would return,
  for a viewset whose `get_queryset()` is itself a security boundary rather than a convenience.

### Fixed

- `HistoryMixin.history()` hardcoded a `pk=None` parameter, so a viewset with a custom
  `lookup_field` (e.g. `lookup_field = "schema_name"`) raised `TypeError` before reaching a line
  of it - a 500, not a 4xx. Now takes `*args, **kwargs` like any other DRF detail action.
  `history()`/`history_list()` also gained their own docstrings, instead of falling back to the
  viewset's when a schema generator asks for one.
- `HistoryMixin`'s auto-built `FilterSet` silently ignored a value that failed a declared filter's
  own validation (e.g. `?created_after=not-a-date`, or `?actor=<uuid>` against the built-in
  integer-typed filter) and just answered as if unfiltered - django-filter's own default behavior.
  It now raises a 400 naming the rejected value instead. `context_filter()`'s own docstring points
  at `filter_cls=` for a project whose actor pks aren't integers, rather than isik guessing a type.

## [0.5.1] - 2026-08-30

### Fixed

- A `ContextField` named `"actor"` (`isik.django.apps.common.db`) - a `ForeignKey` one, producing
  a real `actor_id` column - collided with `generic_history_serializer()`'s own reserved
  `actor_id` name and raised `ImproperlyConfigured`, with no way to resolve it (`track_events(
  exclude=[...])` only excludes tracked *model* fields, not context fields). The real column now
  wins instead: `generic_history_serializer()`/`HistoryMixin` serialize `actor_id` from it,
  typed and indexed, and skip the `pgh_context` JSON annotation they'd otherwise fall back to.
- `generic_history_serializer()`'s `changes` (`isik.django.drf.serializers`) could include a
  `ContextField` column (e.g. `actor_id`) as though it were a change to the tracked object -
  pghistory diffs every non-`pgh_`-prefixed column on the event row generically, so a handoff
  between two actors with no real field edit reported `{"actor_id": [alice.pk, bob.pk]}`. Context
  field keys are now filtered out of `changes` - they record who acted, not what changed.

## [0.5.0] - 2026-08-29

### Added

- `ContextField`/`track_events(context_fields=[...])` (`isik.django.apps.common.db`) - real,
  indexed columns on a `@track_events()` event model, stamped from `pghistory.context()`/
  `HistoryMiddleware`'s own request-scoped context via one combined `BEFORE INSERT` trigger,
  instead of a `pgh_context__<key>` JSON lookup done at query time. Composes with
  `track_events(meta={"indexes": [...]})` for a composite index across multiple context fields -
  nothing isik-specific, `meta=` and `context_fields=` both just feed the same
  `pghistory.track()`/`create_event_model()` call.

### Changed

- `BaseModel.created_at`/`updated_at` (`isik.django.apps.common.db`) are now maintained at the
  database level instead of Django's `auto_now_add`/`auto_now`, which only fire from
  `Model.save()` and left `updated_at` silently stale after `QuerySet.update()`/`bulk_update()`/
  raw SQL. `created_at` gets `db_default=Now()` plus a trigger refusing any UPDATE that changes
  it; `updated_at` is stamped by a `BEFORE UPDATE` trigger on every UPDATE regardless of how it
  was issued. **Breaking**: requires `pgtrigger` in `INSTALLED_APPS` (already installs as
  `django-pghistory`'s dependency) - `BaseModel` raises `ImproperlyConfigured` at import time if
  it's missing. No migration path is provided for existing rows; a project adopting this takes
  its own migration.

## [0.4.1] - 2026-08-10

### Fixed

- `context_filter()`'s default filter (`isik.django.drf.viewsets`): `NumberFilter` cleans into a
  `Decimal`, which psycopg's JSON parameter adapter can't serialize when comparing against a
  pghistory context key transform - now defaults to an `IntegerField`-based filter instead.

### Changed

- Removed dead code exposed by mutation testing: `orm.starts_with()`'s redundant `output_field`
  (`Case` already infers it), `AutoGenericForeignKey`'s no-op `self.name = name` and `db_index=True`
  kwarg (both always overwritten/defaulted downstream), `CreateOnlyFieldsMixin`'s no-op
  `required=False` (DRF's own `include_extra_kwargs()` strips it once `read_only` is set), and the
  `target_name`/`target_related_name` defaults duplicated on `tags()`/`notes()`/`votes()`/
  `bookmarks()`/`comments()`'s internal `_XxxField.__init__` classes (now required kwargs, since
  their public makers always forward explicit values).
- Closed out the mutation-testing gate added in 0.4.0 from 654 surviving mutants to zero: real
  tests added wherever a mutation was actually observable (field-forwarding methods on the
  votes/bookmarks/notes/comments mixins, unique-constraint/constraint-name correctness asserted
  against live model meta instead of pre-migrated DB schema, exact error-message assertions,
  kwargs-forwarding), everything else marked `# pragma: no mutate` only once proven equivalent.

## [0.4.0] - 2026-08-10

### Added

- `event_model_for`/`history_middleware_installed` (`isik.django.apps.common.db`) - resolve the
  Event model django-pghistory generated for a `@track_events()`-tracked model, and detect
  whether `pghistory.middleware.HistoryMiddleware` is installed.
- `generic_history_serializer` (`isik.django.drf.serializers`) - a read-only serializer over a
  `@track_events()`-tracked model's history: `event_id`/`event_created_at`/`action`, a SQL-computed
  `changes` diff against the previous event of the same object, and every tracked field flattened
  at the top level, typed to match the real model field.
- `HistoryMixin`/`context_filter` (`isik.django.drf.viewsets`) - adds two paginated actions to a
  `BaseModelViewSet` for a `@track_events()`-tracked model: `GET <endpoint>/{pk}/history/` (one
  object, governed by the viewset's own permissions) and `GET <endpoint>/history/` (every
  instance, restricted to superusers via `history_list_permission_classes`). Both filterable on
  `action`/`created_after`/`created_before`/`object_id`/`actor` out of the box and extensible via
  `extra_history_filters`.
- Mutation testing via `mutmut` across `isik/`, run in CI (`.github/workflows/mutation.yml`) on
  every push/PR to `master` and gated on zero surviving mutants; genuinely equivalent mutants are
  marked `# pragma: no mutate` instead of chased with a test.

### Fixed

- `FlattenedOneToOneMixin`: a Django `ValidationError` raised by a model-level validator/`clean()`
  on the related object (only reachable via `full_clean()` inside its own `save()`, invisible to
  DRF's automatic per-field validation) is now translated into a DRF `ValidationError` via
  `django_to_drf_validation_error`, instead of surfacing as an unhandled 500.
- `ViewSetRegistryMixin`/`ModelSerializerRegistryMixin`: a class redefining itself under the same
  `__module__`/`__qualname__` (e.g. the same test rerunning in one process, or dev-server
  autoreload) no longer raises `ImproperlyConfigured` - only a genuinely different class claiming
  an already-registered model is treated as a conflict.

## [0.3.0] - 2026-08-03

### Added

- `config.ref()` (and `ref()`/`Ref` in `isik.common.config`) lets a caster's
  `missing_default` or `error_default` fall back to another setting in the same schema -
  e.g. `config.ref("PAGE_SIZE")` or `config.ref(dot="DRF.PAGE_SIZE")` - instead of only a
  static value. Referenced settings are resolved through their own caster/environment
  variable and can chain through further refs; unknown targets, nested-config targets, and
  ref cycles raise `ConfigError`.
- `is_base_class = True` on `RequiredAttributesMixin`, `ViewSetRegistryMixin`, and
  `ModelSerializerRegistryMixin` marks a class as a new base rather than a leaf: it's
  exempted from the required-attributes check without redeclaring `required_attributes`,
  and the two registry mixins fork a private `model_map` for that branch - so a project can
  build several independent `BaseModelViewSet`/`BaseModelSerializer` hierarchies (e.g. one
  per API) without them fighting over one global registry.
- `WriteOnlyFieldsMixin` (`isik.django.drf.serializers`) - `Meta.write_only_fields` marks
  fields settable but never serialized, the `create_only_fields` counterpart for secrets.
  Composed into `BaseModelSerializer`. A field in both `write_only_fields` and
  `create_only_fields` raises `ImproperlyConfigured` at class-definition time instead of an
  opaque assertion error on the first update request.
- `none_during_schema_generation` (`isik.django.drf.viewsets`) - decorator for a
  `get_queryset()` override that returns `self.model.objects.none()` during
  drf-spectacular/drf-yasg schema generation instead of running against
  `self.request.user` (which is `AnonymousUser` at that point).
- `FlattenedOneToOneMixin` (`isik.django.drf.serializers`) - `Meta.flattened_one_to_one_fields`
  exposes a reverse one-to-one relation's fields as if they were declared directly on the
  parent serializer, read and write-through (creates the related row on write if missing,
  updates it in place otherwise, both in one atomic transaction with the parent). Composed
  into `BaseModelSerializer`.
- Documentation across the DRF, feedback, tags, and templated_fields modules, plus a note on
  `BaseModel` about a `django_lifecycle`/`classproperty` interaction that can cause infinite
  recursion.

### Fixed

- `isik.django.drf.pagination.PageNumberPagination` now caps `page_size` at 1000 by
  default, overridable via a `DRF_PAGINATION_MAX_PAGE_SIZE` Django setting or by setting
  `max_page_size` on a subclass - previously a client could request an unbounded page size.
- `ContextLocal._get_var`: lock the create-if-missing branch, closing a race where
  concurrent first access to the same key could orphan a `ContextVar`.
- `user_property`: deny instead of crashing when `request.user` lacks the checked
  attribute/property (e.g. `AnonymousUser`).
- `UsernameOREmailModelBackend`: return `None` instead of crashing with
  `MultipleObjectsReturned` on an empty/`None` username.
- `serializer_method_include`: pass `None` through instead of crashing on `_path_override`
  when the wrapped getter returns a nullable relation.
- `BaseModel`: snapshot field values before lifecycle hooks run and widen `update_fields`
  with anything a hook (or `full_clean`) mutates, so hook-driven changes to fields outside
  `update()`'s own kwargs are no longer silently dropped from the `UPDATE`.
- `FakeErrorSerializer`: resolve fields by instantiating the source serializer instead of
  reading `Meta.fields` directly - now works with `Meta.exclude`, `Meta.fields = "__all__"`,
  and plain serializers with no `Meta` at all.
- `FilterSetMixin.filterset_class`: cache the built class per subclass instead of rebuilding
  it on every access.
- `purge_iterable`: build the exclusion set once instead of once per iteration.
- `enabled_if`: wrap the original function with `@wraps` in the disabled branch instead of
  returning a placeholder disconnected from it entirely.
- `CookieORHeaderSessionMiddleware`: raise `ImproperlyConfigured` at middleware init if
  `SESSION_HEADER_NAME` is missing, instead of crashing on request.
- `IsAuthenticatedANDSignupCompleted`: raise `ImproperlyConfigured` instead of a bare
  `AttributeError` when the user model never defines `SIGNUP_COMPLETED_FIELD`.

## [0.2.0] - 2026-07-24

### Added

- `feedback` app: `votes()`/`bookmarks()`/`notes()`/`comments()` model makers attach a
  per-host interaction model via `contribute_to_class()`, no migration to hand-write; each
  ships a `UserXMixin` (`user.upvote(post)`, `user.add_note(post, "...")`) and a
  `generic_x_serializer()`/`generic_x_field()` DRF helper.
- `tags` app: `tags()` attaches a per-host `Tag` pool plus an M2M through-table, deduped by
  name; `add_tag()`/`remove_tag()`/`set_tags()`/`tag_names()`, a
  `generic_tag_field()`/`generic_tag_serializer()` DRF pair, and name validators enforced
  through the shared `get_tag()` choke point so no write path can slip an invalid name
  through.
- `templated_fields` app: `TemplateCharField`/`TemplateTextField` store a Jinja template
  rendered on demand against a caller-supplied context; sandboxed via Jinja's
  `SandboxedEnvironment` plus an AST-based `TemplatePolicy` allowlist
  (loops/conditionals/macros/filters/tests) with resource limits; DRF integration renders
  `{"raw", "rendered"}` automatically and adds a live-preview viewset action.
- `isik.django.drf` gains `serializers/`, `viewsets/`, `utils/` packages and `schema.py`:
  model/viewset registries, conditional include/only/exclude, create-only fields, a
  Meta-combining serializer mixin, current-user default, lazy relations, a related-count
  field, and filterset/ordering/protected-destroy viewset mixins.

### Changed

- `common` app restructured into `admin/`, `db/`, `email/`, `skippable_validators/`
  packages, plus shared `_model_makers.py` plumbing used by both `feedback` and `tags`.

### Fixed

- `parse_and_check()` only catches exceptions prosemirror-py actually raises
  (`ValueError`/`AssertionError`), not Django's `ValidationError`.

## [0.1.0] - 2026-07-12

Initial release: `isik`, a personal toolkit of everyday Python/Django/DRF utilities.

### Added

- `isik.common.utils`: `noop`, `identity`, `with_attrs`, `returns`, `raises`,
  `camel_to_snake`, `snake_to_pascal`, `snake_to_human`, `require_exclusive_keys`,
  `not_none`, `first_of`, `cloned`, `Sentinel`, `purge_iterable`, `purge_mapping`,
  `all_combinations`, `TransformExceptions`, `SuppressAndRun`, `suppress_callable`,
  `ThreadLocal`, `ThreadLock`, `ContextLocal`, `get_cached`, `enabled_if`.
- `isik.django.apps.common`: `BaseAdmin` (DALF + django-object-actions base admin) with an
  `action()` decorator; `UsernameOREmailModelBackend` auth backend; `AutoGenericForeignKey`;
  `MediaWhiteNoiseMiddleware`; `CookieORHeaderSessionMiddleware`; `BaseModel` (skippable
  validators + django-lifecycle base model); `starts_with()`/`get_object_or_none()` ORM
  helpers; `track_events()` for django-pghistory; `mjml_template()`/`text_template()` email
  renderers; `AllowedCharactersValidator`; skippable validators
  (`make_skippable`, `SkipFieldValidators`, `SkipNamedValidators`,
  `SkippableValidatorsMixin`).
- `isik.django.drf`: `make_filters()`; `PageNumberPagination`; permissions (`ReadOnly`,
  `IsAnonymous`, `IsSuperUser`, `IsAuthenticatedANDSignupCompleted`, `is_owner()`,
  `prevent_actions()`, `user_property()`).
- `isik.sentry.utils`.
- `django_to_drf_validation_error`, a utility turning Django validation errors into DRF
  validation errors.

### Changed

- `exceptions.py` renamed to `error_handling.py`; `TransformExceptions` now supports
  two-step building - skip passing `transform` and instead decorate a named function later,
  useful when the transform is more than a one-liner.

### Fixed

- Generic foreign key resolution now finds models from strings properly.
- `MediaWhiteNoise` internal methods updated to match the latest Whitenoise version.
- Skippable validators now use the class-prepared signal to set up the skippable feature,
  compatible with Django's metaclasses.
