# db

- [concurrency](concurrency.md) — `ConcurrencyToken`, `concurrency_relations`, `with_concurrency_etags`, `concurrency_token_problems`, `assert_concurrency_field_changes`, `concurrency_etag` - the field a row's version is read from
- [conditions](conditions.md) — `AnyChange`, `AnyDontChange`, `AllChange`, `AllDontChange`, `kept_field_condition_problems` - trigger conditions leaving `version` and `updated_at` out
- [constraints](constraints.md) — `as_condition`, `no_database_form`, `python_only_validator` - a validator's rule at the column
- [history](history.md) — `track_events`, `ContextField`, `context_fields_trigger`, `event_model_for`, `history_index_problems`, `history_middleware_installed`, `open_history_context` - pghistory-backed model history
- [lookups](lookups.md) — registers the `length` field lookup
- [models](models.md) — `BaseModel`, `VersionedModel`, `database_kept_fields`, `touch_rows`, `new_primary_key`, `uuid7`
- [orm](orm.md) — `starts_with`, `get_object_or_none`, `with_python_defaults`
- `documentation.py` — `documentation_problems(app_configs, exemption_types)`: fields without `help_text` or `db_comment`, what the system check `isik.E010` reports (see [checks](../checks.md))
