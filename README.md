# isik

Everyday Python utilities, and a set of Django and Django REST Framework building blocks: base models
with history tracking, per-host feedback and tag models attached without hand-written migrations,
scoped permissions, serializer and viewset mixins, and HTTP exceptions you can raise from anywhere.

## Install

```bash
pip install isik              # isik.common only - no dependencies
pip install isik[django]      # isik.django.apps, isik.django.http_exceptions
pip install isik[drf]         # isik.django.drf - brings the django extra with it
pip install isik[celery]      # isik.django.celery - brings the django extra with it
pip install isik[all]         # everything, sentry/tiptap/templated_fields included
```

Requires Python 3.11+. The `django` and `drf` extras assume **PostgreSQL**: `BaseModel`'s timestamps
and history tracking run on database triggers
([django-pgtrigger](https://github.com/AmbitionEng/django-pgtrigger),
[django-pghistory](https://github.com/AmbitionEng/django-pghistory)), so add `pgtrigger` and
`pghistory` to `INSTALLED_APPS`. Bring your own database driver (`psycopg` or `psycopg2`); isik doesn't
choose one for you.

## Documentation

The full index is [docs/INDEX.md](https://github.com/isik-kaplan/isik/blob/master/docs/INDEX.md).

- [isik.common](https://github.com/isik-kaplan/isik/blob/master/docs/common/README.md) - caching,
  concurrency, error handling, functional helpers, typed settings from environment variables
- [isik.django.apps](https://github.com/isik-kaplan/isik/blob/master/docs/django/apps/common/README.md) -
  `BaseModel`, history tracking, and the `votes()`/`comments()`/`notes()`/`bookmarks()`/`tags()` makers
- [Idempotency keys](https://github.com/isik-kaplan/isik/blob/master/docs/django/apps/idempotency/README.md) -
  `Idempotency-Key` on DRF views: a retry replays the first response instead of doing the work twice
- [isik.django.drf](https://github.com/isik-kaplan/isik/blob/master/docs/django/drf/README.md) - permissions
  (`guarding`, `django_permission`, ...), request policies on any view, serializer and viewset mixins,
  filters, pagination
- [isik.django.celery](https://github.com/isik-kaplan/isik/blob/master/docs/django/celery/README.md) - a task
  base whose history rows name whoever caused the task, not nobody
- [isik.django.http_exceptions](https://github.com/isik-kaplan/isik/blob/master/docs/django/http_exceptions/README.md) -
  raise an HTTP status directly instead of threading responses back up the call stack
- [isik.sentry](https://github.com/isik-kaplan/isik/blob/master/docs/sentry/README.md) - Sentry-reporting
  exception suppression
- [Translations](https://github.com/isik-kaplan/isik/blob/master/docs/translations.md) - every
  user-facing string is translatable, through Django's catalogs or stdlib gettext

### Plain Python, and worth knowing about in a Django project

A Django project's imports all start with `isik.django`, so `isik.common.utils` is easy to never
see. It needs no Django and these come up in Django code constantly:

- `first_of(iterable, default=None, pred=None)` / `not_none` - the first item, or the first one
  matching a predicate, without a `next(...)` and a `StopIteration` to handle.
- `with_attrs(**attrs)` - set attributes on a function as a decorator (`short_description`,
  `boolean` on an admin method).
- `returns(value)` / `raises(exception)` / `noop` / `identity` - small callables for defaults and
  hooks.
- `DeclaredString` - a `str` sentinel whose reason, validation and attributes are declared:
  `Exemption("why")`, `NoComment("why")`.
- `TransformExceptions` / `SuppressAndRun` - turn one exception into another, or run something
  when one is swallowed.

See [isik.common](https://github.com/isik-kaplan/isik/blob/master/docs/common/README.md) for all
of it.

What changed in each release: [CHANGELOG.md](https://github.com/isik-kaplan/isik/blob/master/CHANGELOG.md).

## License

MIT
