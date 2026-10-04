# common

Shared Django app: base model/admin classes, ORM helpers, and cross-cutting middleware/backends
used by isik's other Django apps.

Plain-Python helpers that come up in Django code just as often live in `isik.common.utils` -
`first_of`, `not_none`, `with_attrs`, `returns`, `DeclaredString`: see
[common/utils](../../../common/utils/README.md).

- [model_makers](model_makers.md) — shared plumbing behind feedback/tags' model-generating "makers"
- [admin/](admin/README.md) — `BaseAdmin` and the `action` decorator
- [backends/](backends/auth.md) — `UsernameOREmailModelBackend` auth backend, `login_through()`
- [db/](db/README.md) — `BaseModel`, history tracking, lookups, ORM helpers
- [email/](email/templates.md) — MJML/text email template rendering
- [fields/](fields/generic_foreign_key.md) — `AutoGenericForeignKey`
- [middleware/](middleware/README.md) — the `Middleware` base, media serving and cookie/header session middleware
- [skippable_validators/](skippable_validators/README.md) — selectively bypass model field validators

`apps.py` is Django `AppConfig` boilerplate — `CommonConfig.ready()` imports `db.lookups` so the
`length` lookup registers on startup.
