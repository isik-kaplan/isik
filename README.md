# isik

Everyday Python utilities, and a set of Django and Django REST Framework building blocks: base models
with history tracking, per-host feedback and tag models attached without hand-written migrations,
scoped permissions, serializer and viewset mixins, and HTTP exceptions you can raise from anywhere.

## Install

```bash
pip install isik              # isik.common only - no dependencies
pip install isik[django]      # isik.django.apps, isik.django.http_exceptions
pip install isik[drf]         # isik.django.drf - brings the django extra with it
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
- [isik.django.drf](https://github.com/isik-kaplan/isik/blob/master/docs/django/drf/README.md) - permissions
  (`guarding`, `django_permission`, ...), serializer and viewset mixins, filters, pagination
- [isik.django.http_exceptions](https://github.com/isik-kaplan/isik/blob/master/docs/django/http_exceptions/README.md) -
  raise an HTTP status directly instead of threading responses back up the call stack
- [isik.sentry](https://github.com/isik-kaplan/isik/blob/master/docs/sentry/README.md) - Sentry-reporting
  exception suppression
- [Translations](https://github.com/isik-kaplan/isik/blob/master/docs/translations.md) - every
  user-facing string is translatable, through Django's catalogs or stdlib gettext

What changed in each release: [CHANGELOG.md](https://github.com/isik-kaplan/isik/blob/master/CHANGELOG.md).

## License

MIT
