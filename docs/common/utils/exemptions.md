# exemptions

`Exemption`: a `str` that skips one named rule and carries the reason it does. The reason is held to
a minimum length, so it tells the next reader something. A project's opt-outs ("this field needs no
help_text", "this view needs no transaction") are made with it, as are isik's own. Every opt-out can
then be listed by the rule it skips.

```python
from isik.common.utils.exemptions import Exemption, exemption_class


class NoHelpText(
    Exemption,
    rule="schema-docs.help-text",
    why="Every field should say what it holds in help_text.",
    shows_as="",
):
    pass


# The same class, in one line:
NoComment = exemption_class(
    "NoComment",
    rule="schema-docs.db-comment",
    why="Every column should say what it holds.",
    shows_as="",
)


class NotAtomicReason(Exemption, rule="transactions.atomic-requests", why="Every request runs in a transaction.", min_length=30):
    pass


language = models.CharField(..., help_text=NoHelpText(reason="the labels of its choices already say what it holds"))

NoHelpText(reason="it is obvious")
# ValueError: NoHelpText needs a reason of at least 40 characters. Every field should say what it holds
# in help_text. Got 'it is obvious'.
```

`exemption_class()` makes the class in the module that calls it, which is the module migrations
import it from. Pass `module=` when the class is importable from somewhere else.

## The class keywords

- `rule` is the stable dotted slug of the rule it exempts from (`[a-z0-9-]+(\.[a-z0-9-]+)*`). It is
  required and unique: a second type claiming a rule is refused at import, naming both classes. A
  subclass that names no rule keeps its parent's, which is how a floor is raised:
  `class StrictNoHelpText(NoHelpText, min_length=60)`.
- `why` says what the rule asks for and why it matters. It is required, it becomes the class
  docstring unless the class has its own, and every refusal repeats it, so the error teaches the rule
  to whoever is trying to skip it.
- `min_length` is the shortest reason accepted, 40 by default. Whitespace runs collapse before
  counting.
- `shows_as` is what the `str` is. By default it's the reason itself. Use `""` for a sentinel that
  Django and drf-spectacular must see as empty (`help_text`, `db_comment`) while it still carries its
  reason.

## Making one

Only `reason=`, as a keyword: `NoHelpText(reason="...")`. A positional reason, any other keyword, a
missing reason, and the `Exemption` base itself are all refused.

An exemption is equal to, and hashes as, the `str` it shows as. `copy` and `deepcopy` return it as
is, and `pickle` rebuilds it. `deconstruct()` makes Django's migration writer spell it as the call that
made it, `module.NoHelpText(reason='...')`, rather than as `""`. Without that, the migration state would
hold the empty string and `makemigrations` would never settle. `repr()` reads as the call too.

## Listing them

Every type registers its rule when the class is made. Every exemption records the file and line that
made it: the nearest frame outside isik and the installed libraries. An exemption a migration rebuilds
is a replay rather than a new declaration, so it isn't recorded.

- `exemption_types()` returns `{rule: type}`.
- `declared_exemptions(rule=None)` returns every exemption made so far, or only `rule`'s.
- `assert_exemption_budget(rule, at_most=3)` fails if `rule` has more exemptions than that. Adding
  one then means changing a test in review instead of slipping in unnoticed.
- `unseen_exemption_calls(paths)` returns `[(file, line, type name)]` for each `<Type>(reason=...)`
  call under `paths` that never ran, e.g. one inside a function. "Every exemption" means every one,
  not just every one that happened to execute.

In a Django project, `manage.py exemptions` puts these together. It loads the models and the urlconf,
then lists every exemption by rule. Each `RequestPolicy`'s `{action: reason}` exemptions on routed
views appear under `policy.<policy-name>`:

```console
$ python manage.py exemptions
schema-docs.help-text         apps/users/models/user.py:41   the labels of its choices already say what it holds
transactions.atomic-requests  apps/files/views/upload.py:18  streams to storage the whole time
policy.organization-is-set-up apps/orgs/views.py:12          me - answers who is asking, which a blocked caller needs

$ python manage.py exemptions --rule schema-docs.help-text   # one rule
$ python manage.py exemptions --rules                        # each rule, its count, and its why
$ python manage.py exemptions --format json                  # for tooling and CI diffing
```

The command reports calls in the project's own apps that never ran on stderr (in JSON, under
`"unseen"`).

## isik's own opt-outs

Each single opt-out isik offers has its own type, with a rule under `isik.`:

| Opt-out | Type | Rule |
|---|---|---|
| `ViewSetRegistryMixin.exempt_from_registry` | `ViewSetRegistryExemption` | `isik.viewset-registry` |
| `ModelSerializerRegistryMixin.exempt_from_registry` | `SerializerRegistryExemption` | `isik.serializer-registry` |
| `@writes_no_guarded_fields(...)` | `WritesNoGuardedFields` | `isik.guarded-fields.writes-none` |

They take the reason as a plain `str` of at least 40 characters, which becomes that type, or as an
instance of the type. `True`, a short reason and another type's exemption are refused when the class
is defined.

`{name: reason}` maps (`unguarded_fields`, `<policy>_exempt_actions`, `idempotency_exempt_actions`,
...) have no floor. The policy or field and the name already give the context, so
`"as above, for one of them"` is a fine reason there. A blank one is still refused, and so is a
placeholder (`n/a`, `tbd`, `x`, `-`, ...).
