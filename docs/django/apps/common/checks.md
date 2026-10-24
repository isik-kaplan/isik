# checks

isik's own system checks. Each is on by default, and each is tuned or turned off in `ISIK_CHECKS`,
named by enums rather than strings - `Check`, `CheckOption` and `Severity`, all from
`isik.django.apps.common.checks`:

```python
from isik.django.apps.common.checks import Check, CheckOption, Severity

ISIK_CHECKS = {
    Check.DOCUMENTATION: {CheckOption.APPS: ["billing", "accounts"]},
    Check.IDEMPOTENCY: {CheckOption.URLCONFS: ["config.urls.api"]},
    Check.NOT_ATOMIC: {CheckOption.SEVERITY: Severity.WARNING},
    Check.HISTORY_INDEXES: {CheckOption.ENABLED: False},
}
```

They're plain `Enum`s, not `StrEnum`s, so a string never stands in for one: `"idempotency"` as a key, or
`"warning"` as a severity, is an error (`isik.E012`) naming the member to write instead. The module
imports nothing of a project's, so a settings file can import it.

| Check | Id | Refuses |
|---|---|---|
| `Check.VALIDATORS` | `isik.E001` | a validator that doesn't say whether it holds at the column ([constraints](db/constraints.md)) |
| `Check.CONCURRENCY_TOKENS` | `isik.E002` | a precondition view comparing a token a write can keep ([preconditions](../../drf/viewsets/preconditions.md)) |
| `Check.PRECONDITION_OPTIONS` | `isik.E003`, `isik.W001` | precondition options that are invalid, or undo each other |
| `Check.RELATION_LOCKS` | `isik.W002` | a relation a version covers whose lock trigger isn't installed ([concurrency](db/concurrency.md)) |
| `Check.VERSION_READS` | `isik.E004` | a write asking `If-Match` with no read handing out the version |
| `Check.IDEMPOTENCY` | `isik.E005` | a routed write that honors no `Idempotency-Key` ([coverage](../../drf/coverage.md)) |
| `Check.SINGLETONS` | `isik.E006` | a flattened singleton no unique constraint proves ([flattened_singleton_related](../../drf/serializers/flattened_singleton_related.md)) |
| `Check.PRECONDITIONS` | `isik.E007` | a routed write to one object that asks no `If-Match` ([coverage](../../drf/coverage.md)) |
| `Check.HISTORY_INDEXES` | `isik.E008` | an event model without its `(pgh_obj, -pgh_id)` index ([history](db/history.md)) |
| `Check.EXEMPTIONS` | `isik.E009` | an exemption loading the project didn't see ([exemptions](../../../common/utils/exemptions.md)) |
| `Check.DOCUMENTATION` | `isik.E010` | a field without `help_text` or `db_comment` |
| `Check.NOT_ATOMIC` | `isik.E011` | a view served outside `ATOMIC_REQUESTS` that doesn't say why ([atomic](atomic.md)) |

## Options

- `CheckOption.ENABLED` - `True` by default; `False` turns the check off.
- `CheckOption.SEVERITY` - `Severity.ERROR` or `Severity.WARNING`, over the check's own. The messages
  keep their ids, so a check run as a warning still reports as `isik.E0xx` - integrate with
  `Severity.WARNING`, fix what it reports, then remove the line to make it an error again.
  `manage.py check` fails only on errors unless run with `--fail-level WARNING`.
- `CheckOption.URLCONFS` - for a check over routed views: the urlconfs to walk, one or a list. Every urlconf the
  project serves by default (`project_urlconfs()`: `ROOT_URLCONF`, and each django-hosts host's).
  A list leaving a served one out is a warning, `isik.W003`, naming it - a check that can't see a
  urlconf passes it without saying so otherwise.
- `CheckOption.APPS` - for a check over models (`Check.HISTORY_INDEXES`, `Check.DOCUMENTATION`): app labels. The project's
  own apps by default - `project_app_configs()`: every installed app not installed as a library, and
  not isik's.
- `CheckOption.EXEMPTION_TYPES` - for `Check.DOCUMENTATION`: the `Exemption` types, or their dotted paths, that answer
  for a field. Any `Exemption` by default.
- `CheckOption.INHERITED_FROM_LIBRARIES` - for `Check.DOCUMENTATION`: `True` by default, so every field is checked.
  `False` leaves out the fields a model inherits unchanged from an installed library's abstract base -
  `AbstractUser`'s `username`, an OAuth application's `client_id`, a tenant mixin's `schema_name` -
  whose description is the library's to write, not the project's. A field the project declares again,
  and one from its own abstract base, are still checked.

A key that isn't a `Check`, an option that isn't a `CheckOption` or that the check doesn't take, or a
severity that isn't a `Severity` - a string for any of them included - is an error: `isik.E012`.

## documentation - `isik.E010`

Every field of the project's own models says what it holds: in `help_text`, which the API schema and
the admin show, and - for a field with a column of a managed model - in `db_comment`, which someone
reading the database sees. An exemption answers for either:

```python
class NoHelpText(Exemption, rule="schema-docs.help-text", why="Every field says what it holds."):
    pass


language = models.CharField(
    choices=LANGUAGES,
    help_text=NoHelpText(reason="the labels of its choices already say what it holds"),
    db_comment="The language the user reads in.",
)
```

```python
ISIK_CHECKS = {Check.DOCUMENTATION: {CheckOption.INHERITED_FROM_LIBRARIES: False}}
```

Left out: fields Django adds itself (an implicit `id`, a parent link), a many-to-many's `db_comment`
(it has no column), and proxies, auto-created models, and the models isik or pghistory generate.
`documentation_problems(app_configs, exemption_types)` (`isik.django.apps.common.db.documentation`)
lists the fields.

## Upgrading to 0.30

Every check is on, and the new ones (`E005`-`E011`) are errors. A project adopting 0.30 either fixes
what they report, or runs it as a warning - `{CheckOption.SEVERITY: Severity.WARNING}` - or turns it
off, with the reason in a comment beside it, until it can.
