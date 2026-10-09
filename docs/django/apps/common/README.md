# common

Shared Django app: base model/admin classes, ORM helpers, and cross-cutting middleware/backends
used by isik's other Django apps.

Plain-Python helpers that come up in Django code just as often live in `isik.common.utils` -
`first_of`, `not_none`, `with_attrs`, `returns`, `Exemption`: see
[common/utils](../../../common/utils/README.md).

- [model_makers](model_makers.md) — shared plumbing behind feedback/tags' model-generating "makers"
- [admin/](admin/README.md) — `BaseAdmin` and the `action` decorator
- [backends/](backends/auth.md) — `login_through()`, `listed_backend_path()`
- [db/](db/README.md) — `BaseModel`, history tracking, lookups, ORM helpers
- [email/](email/templates.md) — MJML/text email template rendering
- [fields/](fields/generic_foreign_key.md) — `AutoGenericForeignKey`
- [middleware/](middleware/README.md) — the `Middleware` base, media serving and cookie/header session middleware
- [skippable_validators/](skippable_validators/README.md) — selectively bypass model field validators
- `urlconfs.py` — `project_urlconfs(urlconf=None)`: `ROOT_URLCONF` plus each django-hosts host's urlconf
  when django-hosts is installed, or the urlconfs given; what the coverage walk and `manage.py exemptions` load
- `manage.py exemptions` — every exemption by rule; see [exemptions.md](../../../common/utils/exemptions.md)

`apps.py` is Django `AppConfig` boilerplate — `CommonConfig.ready()` imports `db.lookups` so the
`length` lookup registers on startup.
