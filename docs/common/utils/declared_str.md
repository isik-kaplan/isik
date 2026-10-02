# declared_str

`DeclaredStr` - a `str` whose value, validation and carried attributes are declared on the class,
for the sentinel that otherwise gets a hand-written `__new__` every time: an exemption that must say
why, a field description that must be empty to Django while still carrying its reason, a permission
name that must match a shape and carry a label.

```python
from isik.common.utils.declared_str import DeclaredStr, attribute, text


class Exemption(DeclaredStr):
    reason = text(min_length=40)


class NoComment(DeclaredStr, displays_as=""):
    reason = text(min_length=40, max_length=300)


class PermissionName(DeclaredStr):
    name = text(pattern=r"[a-z_]+:[a-z_]+:[a-z_]+")
    label = attribute(str)
    delegatable = attribute(bool, default=False)


Exemption("the registry would reject a second viewset over this model")  # truthy, reads as its reason
NoComment("written by the trigger, never by hand")                        # == "" to anything reading a str
NoComment("n/a")    # ValueError: NoComment needs a reason of at least 40 characters, not 'n/a'.
PermissionName("org:invite:member", label="Invite members").delegatable   # False
```

## Declarations

- `text(min_length=None, max_length=None, pattern=None, collapse_whitespace=True)` - text, its
  whitespace collapsed to single spaces first, then held to its lengths and to `pattern` (matched in
  full). The first `text()` a class declares is its positional argument.
- `attribute(type=None, default=...)` - a keyword argument, checked against `type` if given, required
  unless it has a default.

Everything declared is kept on the instance under its own name (`NoComment(...).reason`).

## What the str is

The first `text()`, unless the class says `displays_as=`. That is how a sentinel can be `""` to
Django and drf-spectacular - nothing reaches a column comment or a published description - while a
project's own check still reads `.reason`. A class with neither is refused when it's defined.

It equals, and hashes as, the str it displays as.

## Configuring the rule

Lengths and patterns are per class, and inherited: a project's own base sets them once.

```python
class Reasoned(DeclaredStr):
    reason = text(min_length=40)


class NoHelpText(Reasoned, displays_as=""):
    """This field needs no description in the API, and here is why."""


class NoComment(Reasoned, displays_as=""):
    """This column needs no comment in the database, and here is why."""
```

A subclass redeclaring `reason = text(...)` replaces the rule in place.

## As itself, everywhere

`copy`, `deepcopy` and `pickle` rebuild it through its declarations, and `deconstruct()` makes
Django's migration writer spell it as the call that made it (`NoComment('...')`) rather than as `""` -
otherwise the migration state would hold the empty string, and `makemigrations` would see a change
forever. `repr()` reads as that call too.
