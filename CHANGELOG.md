# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
