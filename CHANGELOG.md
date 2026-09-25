# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.10.0] - 2026-09-25

### Added

- `django_permission(permission, message=None, name=None)` (`isik.django.drf.permissions`) - a
  permission that passes only if the caller holds a Django permission (`request.user.has_perm`). Takes
  a permission name, a `str` subclass, a StrEnum/TextChoices member, or an Enum member whose value is
  one, and passes it to the backends untouched. Request-level only: `ModelBackend` answers `False` to
  every object-level check. An unauthenticated request is refused without asking the backend. Named
  after the permission (`HasInvitationsIssuePublicInvitation`). `message=` takes a template with an
  optional `%(permission)s` placeholder, a fixed message, or a callable asked on refusal as
  `message(permission=, request=, view=)`. By default it names the permission.
- Every user-facing string isik produces is translatable - permission messages, validation errors,
  HTTP exception descriptions (including stdlib's `HTTPStatus` ones in the middleware's response), and
  every raised exception, configuration errors included. With Django they come from Django's own
  catalogs. Without Django, from stdlib gettext's `isik` domain. Errors raised at import time fall back
  to the untranslated text instead of failing with `AppRegistryNotReady`. `isik/locale/isik.pot` lists
  every message for `msgmerge`. See `docs/translations.md`.

### Changed

- The `drf` extra now brings the `django` extra with it. `isik.django.drf` builds on `isik.django.apps`
  (`BaseModel`, history tracking), so `pip install isik[drf]` on its own used to leave imports failing.
- `README.md` is its own page instead of a symlink to `docs/INDEX.md`, whose `docs/`-relative links
  broke from the repo root on GitHub and on PyPI. It links to the docs with absolute URLs.

### Fixed

- `prevent_actions`, `only_actions`, `user_property` and `object_property` built their messages with
  an f-string inside `gettext_lazy`, so the msgid changed with every value and could never be
  translated. They use placeholders now; the rendered English is unchanged.
- `SkippableValidatorsMixin` (and so every `BaseModel`) connected one `class_prepared` receiver per model
  class with `sender=cls`. Django keys those by `id(cls)` and never removes them, so once a model class
  was freed (one defined in a test, or under `isolate_apps`), the next class allocated at the same
  address inherited the receiver and had its validators wrapped without mixing it in - an intermittent
  failure, and one leaked receiver per model. It's now a single receiver that checks what it's handed.
- `BaseModel`'s `id`/`created_at`/`updated_at` `verbose_name`s were translated once, at import, in the
  default language. They're lazy now, so they follow the active language. No migration.

## [0.9.0] - 2026-09-25

### Changed

- **Breaking:** `guarding()`'s value-targeted form is now its own keyword, `setting=`:
  `guarding(is_owner("issued_by"), setting={"names_issuer": True})`. `fields=` takes field names only,
  and a dict passed to it raises `ValueError` pointing at `setting=`. As `fields={...}`, it read as
  "names_issuer: guarded, yes" rather than "guarded when set to True".
- `is_owner(owner_field, of=None)` - both halves take a dotted path or a callable, so an owner can be
  relations away (`is_owner("installation.organization")`) or computed (`is_owner(lambda obj: ...)`).
  A path broken by a missing attribute or a `None` relation means "not the owner". A dotted
  `owner_field` used to be read as one attribute name and so always refused.
- `user_property`/`object_property` take the model's own descriptor, or a name, as the first
  argument: `user_property(User.is_staff)`, `user_property(User.quota)`, `user_property("is_app")`.
  Properties, model fields, forward and reverse relations, and both `functools`' and Django's
  `cached_property` all resolve to the attribute they read, which is then read off the instance, so
  a `cached_property` keeps its cache (a `property_=` used to be called through its raw `fget`). A
  reverse relation resolves to its accessor, not to the field on the other model. Anything with no
  name to find, like a plain class attribute's value, raises `TypeError`. `property_=`/`attribute=`
  still work.
- **Breaking:** generated permission classes are named like classes, not like the call that made
  them: `is_owner("issued_by")` is `IsOwnerByIssuedBy` (was `IsOwnerPermission(owner_field=issued_by)`),
  `is_owner("tenant", of="organization")` is `IsOwnerByTenantOfOrganization`, `user_property("is_verified")`
  is `UserIsVerified`, `object_property("is_app")` is `ObjectIsApp`, `prevent_actions("create", "destroy")`
  is `PreventCreateAndDestroy`, and `guarding(~object_property("is_app"), actions=["destroy"])` is
  `NotObjectIsAppForDestroy` (compositions are named too - `AOrB`, `AAndB`, `NotA` - where they used
  to show up as `SingleOperandHolder`). Nothing reads these names but people and error messages.

### Fixed

- A field guard no longer raises `ImproperlyConfigured` (a 500) when the request can't write the
  guarded field. That happened when `?only=`/`?exclude=` dropped it (a client could trigger it) or when
  the action's serializer from `serializer_class_action_map` doesn't carry it. Such a field is skipped
  now. A name that none of the viewset's serializers has still raises, as a typo.

### Added

- `name=` on every factory that builds a class for its caller: `is_owner`, `user_property`,
  `object_property`, `prevent_actions`, `guarding`, `generic_vote_serializer`/`generic_comment_serializer`/
  `generic_note_serializer`/`generic_bookmark_serializer`, `generic_tag_serializer`,
  `generic_history_serializer` and `FakeErrorSerializer`. Always optional; the defaults are unchanged
  apart from the permission names above.
- `model_name=` on `votes()`/`comments()`/`notes()`/`bookmarks()`, and `tag_model_name=`/
  `through_model_name=` on `tags()`, to name the generated models. Defaults unchanged, so existing
  tables and migrations are untouched.
- `guarding.values(*values)` - a `setting=` target matching any of several values:
  `setting={"status": guarding.values(Status.APPROVED, Status.FEATURED)}`. Every target is normalised
  to a `GuardTarget` when the guard is built (`ANY_VALUE` for `fields=`, `EqualsTarget` for a plain
  value, `OneOfTarget` for `guarding.values`); subclass `GuardTarget` for a custom one.
- Public building blocks, usable on their own: `guarding_values` (what `guarding.values` is),
  `GuardTarget`/`EqualsTarget`/`OneOfTarget`/`AnyValueTarget`, `evaluate_permission` (a permission
  tree as one predicate, three-valued without an object), `descriptor_attribute_name`, and
  `permission_name` (a class-style name for any permission entry, compositions included).
- Field guards can't be skipped by overriding a handler. `FieldGuardsOnSaveMixin` (part of
  `BaseModelSerializer`) runs the current viewset's field guards inside `save()`, before the write,
  however the serializer was built, and counts `save(**kwargs)` values as part of the write. Any
  other unsafe request that succeeds without its guards having run (a plain serializer saved by
  hand, an ORM write in a custom action) raises `ImproperlyConfigured` inside `transaction.atomic()`
  on every database (`field_guard_databases` narrows it), so its database writes roll back.
  `@writes_no_guarded_fields` marks an action that writes no guarded field. `destroy` is exempt via
  `actions_writing_no_guarded_fields`. A request DRF answers from an exception (a refused guard, a
  failed validation) rolls back whatever the handler wrote before it, `on_commit` callbacks
  included. The save hook only checks serializers for the viewset's own model.
  `check_guarded_fields()` now also takes `save_kwargs` and checks `many=True` writes row by row.
- `only_actions(*actions, name=None)` - the complement of `prevent_actions`: allows only the given
  actions (`OnlyListAndRetrieve`). Refuses to be built with none.
- `guarding.other_than(*values)` and `guarding.matching(predicate)` - `setting=` targets for "anything
  but these" and "whatever this callable accepts" (`NotOneOfTarget`/`MatchingTarget`, also public as
  `guarding_other_than`/`guarding_matching`).
- Shared-serializer check: when two `GuardedFieldsMixin` viewsets share a serializer and only one guards
  a field, defining the second raises `ImproperlyConfigured` naming both. A viewset that leaves the
  field open on purpose lists it in the new `unguarded_fields` attribute.
- `words_to_pascal(text)` (`isik.common.utils`) - PascalCase from any run of words, splitting on every
  non-alphanumeric character and keeping inner capitals, so already-PascalCase names survive
  (`snake_to_pascal("IsSuperUser")` gives `"Issuperuser"`).

## [0.8.0] - 2026-09-25

### Added

- `guarding(predicate, fields=... | actions=..., message=None)` (`isik.django.drf.permissions`) - narrows
  any permission, `&`/`|`/`~` compositions included, to some actions or some fields. `actions=` refuses
  with a 403 where DRF runs permissions. `fields=` refuses a write that actually changes a guarded field
  with a `ValidationError` keyed by that field, checked after validation against the typed value
  (a field echoed back unchanged isn't a change). `fields=` also takes a dict of target values, to
  guard only one direction of a toggle. The predicate is evaluated as one expression over request and
  object, so `~` works on object-level permissions (it refuses everything under DRF's own `NOT`).
  Without an object, object-only parts are unknown and an unknown answer allows. A guard must be a
  top-level `permission_classes` entry: composing one raises `TypeError`/`ImproperlyConfigured`,
  and a `fields=` guard on a viewset that doesn't run field guards raises `ImproperlyConfigured` rather
  than silently permitting everything.
- `GuardedFieldsMixin` (`isik.django.drf.viewsets`) - runs `fields=` guards from `perform_create`/
  `perform_update`, or explicitly via `check_guarded_fields(serializer)`. Composed into `BaseModelViewSet`.
- `object_property(property_=None, attribute=None)` - the `user_property` counterpart for the object
  being acted on, e.g. `guarding(~object_property(attribute="is_app"), actions=["partial_update"])`.
- `is_owner(owner_field, of=None)` - `of` compares `obj.<owner_field>` to an attribute of the user
  (dotted paths allowed) instead of the user itself, for rows owned by a tenant rather than a person.
  The generated class name only changes when `of` is given.

## [0.7.0] - 2026-09-10

### Added

- `DeclaredOrderingMixin`/`DeclaredOrderingFilter` (`isik.django.drf.viewsets.ordering`) - `declared_ordering`
  maps a `?ordering=` key exposed to API clients to the real field(s) (or ordering expression) it
  orders by, for a value that isn't itself a field - a stand-in for several columns, or one that
  shouldn't leak the real column name. Mirrors `FilterSetMixin`'s `declared_filters`: a key's `-`
  counterpart is always valid too and flips the sign of every field it maps to, and
  `DeclaredOrderingFilter` must be present in `filter_backends` (a drop-in `OrderingFilter`
  replacement) or this fails loudly at class-definition time. Composed into `BaseModelViewSet`
  alongside `ReverseOrderingMixin`.

## [0.6.0] - 2026-08-30

### Added

- `validate_inputs(*validators, **kwarg_validators)` (`isik.common.utils`) - decorator validating a
  function's own arguments where the function is declared, before its body runs. Positional
  validators line up with the function's positional parameters left to right (`self`/`cls` skipped
  by name); keyword validators name a parameter directly. A validator is any callable: return falsy
  to fail with a plain `ValueError` naming the argument and its value, or raise your own exception
  to propagate it unchanged instead - no single validator "protocol" required.

## [0.5.5] - 2026-08-30

### Fixed

- `isik.django.drf.spectacular.AutoSchema` no longer advertises the tracked model's own
  `filter_backends`-derived filters (e.g. `is_active`, `created_at__gte`) on `HistoryMixin`'s
  `history()`/`history_list()` routes - passing them there did nothing, since neither action calls
  `filter_queryset()`.
- `include=` is now omitted entirely from a `ConditionalSerializerMixin` serializer's documented
  parameters when it declares no `Meta.relational_fields`, instead of being published with no enum
  (effectively free-text, accepting a value that does nothing).
- A view whose `get_serializer_class()` raises (e.g. `GenericAPIView`'s own default, when
  `serializer_class` was never set) no longer fails `AutoSchema`'s entire document - caught the same
  way drf-spectacular's own equivalent internal call already is.

## [0.5.4] - 2026-08-30

### Added

- `drf-spectacular` is now part of the `drf` extra (`isik[drf]`) - `isik.django.drf.spectacular.AutoSchema`
  wires it project-wide, fixing schema gaps that are properties of isik's own mixins:
  - `HistoryMixin`'s `history()`/`history_list()` are now typed as returning a paginated array, get
    distinct operation ids instead of colliding (they tokenize identically once the path parameter
    is dropped), and their built-in filters appear as query parameters (`history_filterset_class`
    is applied inside the action rather than exposed as `filterset_class`, so drf-spectacular's own
    introspection never saw it otherwise).
  - Any serializer using `ConditionalSerializerMixin` gets `only=`/`exclude=`/`include=` documented
    as query parameters - read straight off the query string, so there was nothing for schema
    introspection to find on its own before. `include=`'s enum comes from `Meta.relational_fields`.

## [0.5.3] - 2026-08-30

### Added

- `context_field_filter(event_model, name)` (`isik.django.drf.viewsets`) - filters `HistoryMixin`'s
  history endpoints on a `ContextField`'s own real, indexed column instead of a `pgh_context` JSON
  lookup (`context_filter()`). `HistoryMixin`'s built-in `actor` filter uses it automatically
  whenever the tracked model has an `actor` `ContextField`, and unlike the JSON-based fallback,
  needs no `pghistory.middleware.HistoryMiddleware` installed.

### Fixed

- `HistoryMixin.check_permissions()` now enforces `history_list_permission_classes` directly
  (moved off `get_permissions()`, which a subclass is far more likely to override wholesale for
  unrelated reasons and silently drop the check by doing so).
- `HistoryMixin.history()`'s docstring no longer leaks its own implementation rationale into the
  public API description a schema generator publishes - trimmed to the one sentence a caller needs.

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
