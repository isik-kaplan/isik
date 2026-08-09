# isik docs

## isik.common

- [common/](common/README.md)
  - [utils/](common/utils/README.md) - caching, concurrency, error handling, functional/iterable
    helpers, metaclasses, sentinels, string case conversion
  - [config/](common/config/README.md) - typed settings objects built from environment variables

## isik.sentry

- [sentry/](sentry/README.md) - Sentry-reporting exception suppression helpers (`sentry` extra)

## isik.django.apps

- [django/apps/common/](django/apps/common/README.md) - `BaseModel`, `BaseAdmin`, history
  tracking, ORM helpers, email templates, middleware, skippable validators, and the shared
  model-maker plumbing behind the apps below
- [django/apps/feedback/](django/apps/feedback/README.md) - `votes()`, `bookmarks()`, `notes()`,
  `comments()`: per-host interaction models attached with no migration to hand-write
- [django/apps/tags/](django/apps/tags/README.md) - `tags()`: a per-host tag pool + M2M
  through-table, deduped by name
- [django/apps/templated_fields/](django/apps/templated_fields/README.md) - model fields storing
  a sandboxed Jinja template, rendered on demand against a caller-supplied context

## isik.django.drf

- [django/drf/](django/drf/README.md) - Django REST Framework helpers: error translation,
  filters, pagination, permissions, ad hoc schema builders, plus serializer/viewset mixins

## isik.django.http_exceptions

- [django/http_exceptions/](django/http_exceptions/README.md) - raise an HTTP status directly
  from anywhere instead of threading `Response`s back up the call stack

## Everything else

- `isik/_internal/` - private helpers, not public API (mentioned inline where used, e.g.
  [sentry/README.md](sentry/README.md))
- `isik/django/apps/*/apps.py` - plain Django `AppConfig` boilerplate, not documented separately
