"""
Exemption - a `str` that skips one named rule and carries the reason it does, held to a floor so the
reason tells the next reader something.

    class NoHelpText(
        Exemption,
        rule="schema-docs.help-text",
        why="Every field should say what it holds in help_text.",
        shows_as="",
    ):
        pass

    language = models.CharField(..., help_text=NoHelpText(reason="the labels of its choices already say it"))

    NoHelpText(reason="it is obvious")
    # ValueError: NoHelpText needs a reason of at least 40 characters. Every field should say what it
    # holds in help_text. Got 'it is obvious'.

`exemption_class("NoHelpText", rule=..., why=..., shows_as="")` makes the same class in one line.

Every type registers its rule, which no other type may claim, and every exemption registers where it
was made - `exemption_types()`, `declared_exemptions()`, `assert_exemption_budget()`, and
`manage.py exemptions` in a Django project, answer "what in this project skips which rule, and why".
"""

import ast
import inspect
import os
import re
import sys
import sysconfig
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path

from isik._internal.translation import gettext as _
from isik.common.utils.sentinel import Sentinel


NOT_GIVEN = Sentinel("EXEMPTION_NOT_GIVEN")


class Requirement(Enum):
    """
    How much a rule asks of an opt-out - set per rule, by its owner's default and by the project:

    - `STRICT` - an exemption with a reason over the type's floor. The default.
    - `OPTIONAL` - that, or one with no reason, or the plain value the slot reads as "opt out"
      (`question=False`, `exempt_from_registry=True`) - still made into an exemption and listed.
    - `OFF` - a check of the rule asks nothing (`enforced()` is False). Refused for a rule whose
      exemption changes what code does, rather than answering a check - there's nothing to turn off.
    """

    STRICT = "strict"
    OPTIONAL = "optional"
    OFF = "off"


# {rule: Requirement}, set by exemption_requirements() - where a project without Django says its levels.
_levels = {}
RULE_PATTERN = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)*")
DEFAULT_MIN_LENGTH = 40

# {rule: type}, every exemption made so far in the order made, {code: name} for each function that
# makes exemptions for its caller (`makes_exemption()`), and the code of each library function whose
# exemptions are the library's own (`makes_own_exemptions()`).
_types = {}
_declared = []
_makers = {}
_own = set()

_ISIK = str(Path(__file__).resolve().parents[2]) + os.sep
# Where installed libraries and the standard library live - a frame there is a metaclass or a decorator
# on the way to the call site, not the call site.
_LIBRARIES = tuple(
    {
        str(Path(sysconfig.get_paths()[name]).resolve()) + os.sep
        for name in ("stdlib", "platstdlib", "purelib", "platlib")
    }
)


def _call_site(frame=None):
    """
    (frame, file) of the code that made an exemption: the nearest frame outside isik and the libraries,
    and not in a function that makes exemptions for its caller (`makes_exemption()`) - or, nearer still,
    a library function making its own (`makes_own_exemptions()`), wherever it lives.
    """
    frame = frame or sys._getframe()
    in_libraries = []
    while frame is not None:
        file = str(Path(frame.f_code.co_filename).resolve())
        if frame.f_code in _own:
            return frame, file
        if not file.startswith(_ISIK) and frame.f_code not in _makers:
            if not file.startswith(_LIBRARIES):
                return frame, file
            in_libraries.append((frame, file))
        frame = frame.f_back
    return in_libraries[0] if in_libraries else (None, None)


def _in_a_migration(frame):
    # A migration rebuilds a field's state, help_text included - replaying an exemption, not making one.
    return ".migrations." in str(frame.f_globals.get("__name__"))


class Exemption(str):
    """
    The base every exemption type subclasses, with its rule as class keywords - see the module docstring:

    - `rule` - the stable dotted slug of the rule it exempts from (`schema-docs.help-text`). Required,
      and unique: a second type claiming a rule is refused. A subclass that names none keeps its parent's.
    - `why` - what the rule asks for and why it matters. Required; it's the class docstring unless the
      class has one, and every refusal says it.
    - `min_length` - the shortest reason accepted, 40 by default, counted after whitespace runs collapse.
    - `shows_as` - what the `str` is: the reason itself by default, or e.g. `""` for a sentinel Django
      must see as empty while it still carries its reason.

    Made with `reason=` only. Equal to, and hashed as, the `str` it shows as; copied and pickled as
    itself, and written into migrations (`deconstruct()`) as the call that made it.
    """

    rule = None
    why = None
    min_length = DEFAULT_MIN_LENGTH
    shows_as = None
    # Listed under the request policy it exempts from, with the action, rather than under its own rule -
    # see isik.django.drf.viewsets.request_policies.RequestPolicyExemption.
    listed_by_policy = False
    # The level its owner gives the rule, and whether its exemption changes what code does - see Requirement.
    requirement = Requirement.STRICT
    changes_behavior = False

    def __init_subclass__(
        cls,
        *,
        rule=None,
        why=None,
        min_length=None,
        shows_as=NOT_GIVEN,
        requirement=None,
        changes_behavior=None,
        **kwargs,
    ):
        super().__init_subclass__(**kwargs)
        if requirement is not None and not isinstance(requirement, Requirement):
            raise TypeError(
                _("%(type)s's requirement must be a Requirement, not %(value)r.")
                % {"type": cls.__name__, "value": requirement}
            )
        if requirement is not None:
            cls.requirement = requirement
        if changes_behavior is not None:
            cls.changes_behavior = bool(changes_behavior)
        if rule is not None:
            if not isinstance(rule, str) or not RULE_PATTERN.fullmatch(rule):
                raise TypeError(
                    _("%(type)s's rule must be a dotted slug like 'schema-docs.help-text', not %(rule)r.")
                    % {"type": cls.__name__, "rule": rule}
                )
            claimed = _types.get(rule)
            # The same class defined again (a test run twice, an autoreload) replaces itself.
            if claimed is not None and (claimed.__module__, claimed.__qualname__) != (cls.__module__, cls.__qualname__):
                raise TypeError(
                    _(
                        "%(type)s claims the rule %(rule)r, which %(module)s.%(claimed)s already exempts from - "
                        "name another."
                    )
                    % {
                        "type": cls.__name__,
                        "rule": rule,
                        "module": claimed.__module__,
                        "claimed": claimed.__qualname__,
                    }
                )
        elif cls.rule is None:
            raise TypeError(
                _("%(type)s needs rule= - the dotted name of the rule it exempts from.") % {"type": cls.__name__}
            )
        if why is None:
            why = cls.why
        if not isinstance(why, str) or not why.strip():
            raise TypeError(
                _("%(type)s needs why= - what the rule asks for, and why it matters.") % {"type": cls.__name__}
            )
        if min_length is not None and (
            not isinstance(min_length, int) or isinstance(min_length, bool) or min_length < 1
        ):
            raise TypeError(
                _("%(type)s's min_length must be a whole number of at least 1, not %(value)r.")
                % {"type": cls.__name__, "value": min_length}
            )
        if shows_as is not NOT_GIVEN and shows_as is not None and not isinstance(shows_as, str):
            raise TypeError(
                _("%(type)s's shows_as must be a str, not %(value)r.") % {"type": cls.__name__, "value": shows_as}
            )
        cls.why = why
        if vars(cls).get("__doc__") is None:
            cls.__doc__ = why
        if min_length is not None:
            cls.min_length = min_length
        if shows_as is not NOT_GIVEN:
            cls.shows_as = shows_as
        if rule is not None:
            cls.rule = rule
            _types[rule] = cls

    def __new__(cls, *args, **kwargs):
        if cls.rule is None:
            raise TypeError(
                _("%(type)s is the base to make exemption types from - subclass it with rule= and why=.")
                % {"type": cls.__name__}
            )
        if args:
            raise TypeError(_("%(type)s takes its reason as reason=, not positionally.") % {"type": cls.__name__})
        unknown = sorted(set(kwargs) - {"reason"})
        if unknown:
            raise TypeError(
                _("%(type)s takes only reason=, not %(names)s.") % {"type": cls.__name__, "names": ", ".join(unknown)}
            )
        strict = requirement_of(cls) is Requirement.STRICT
        if "reason" not in kwargs and strict:
            raise TypeError(_("%(type)s needs reason=.") % {"type": cls.__name__})
        self = cls._made(cls._cleaned(kwargs.get("reason", ""), strict=strict))
        frame, file = _call_site()
        if frame is not None:
            self.file, self.line = file, frame.f_lineno
            self.library = frame.f_code in _own
            if not _in_a_migration(frame):
                _declared.append(self)
        return self

    @classmethod
    def _cleaned(cls, reason, *, strict):
        if not isinstance(reason, str):
            raise TypeError(
                _("%(type)s's reason must be text, not %(value)r.") % {"type": cls.__name__, "value": reason}
            )
        reason = " ".join(reason.split())
        # Not strict: no reason is fine, but one that's given still says something.
        if (strict or reason) and len(reason) < cls.min_length:
            raise ValueError(
                _("%(type)s needs a reason of at least %(length)d characters. %(why)s Got %(reason)r.")
                % {"type": cls.__name__, "length": cls.min_length, "why": cls.why, "reason": reason}
            )
        return reason

    @classmethod
    def _made(cls, reason):
        self = super().__new__(cls, reason if cls.shows_as is None else cls.shows_as)
        self.reason = reason
        self.file = self.line = None
        self.library = False
        return self

    def __bool__(self):
        # Declaring one is the opt-out, reason or not - `if cls.exempt_from_registry:` holds for a reasonless
        # one too. A type that shows as something else ("" for Django to see as empty) is truthy as that is.
        return True if self.shows_as is None else bool(self.shows_as)

    def __reduce__(self):
        return _restored, (type(self), self.reason)

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def deconstruct(self):
        """How Django's migration writer spells it: as the call that made it, not as the str it equals."""
        return f"{type(self).__module__}.{type(self).__qualname__}", (), {"reason": self.reason}

    def __repr__(self):
        return f"{type(self).__name__}(reason={self.reason!r})"


def _restored(cls, reason):
    """An unpickled exemption - the same one again, not a new declaration."""
    return cls._made(reason)


def exemption_class(
    name,
    *,
    rule,
    why,
    min_length=None,
    shows_as=NOT_GIVEN,
    requirement=None,
    changes_behavior=None,
    base=Exemption,
    module=None,
):
    """
    `Exemption` subclassed in one line, in the caller's module - so migrations can import it by name:

        NoHelpText = exemption_class(
            "NoHelpText",
            rule="schema-docs.help-text",
            why="Every field should say what it holds in help_text.",
            shows_as="",
        )

    `module=` names the module it's importable from instead, when it's assigned somewhere else.
    `requirement=` is the rule's level unless a project sets another - see `Requirement`.
    """
    if module is None:
        frame, _file = _call_site()
        if frame is None:
            raise TypeError(
                _("exemption_class() can't tell which module %(name)s is in - pass module=.") % {"name": name}
            )
        module = frame.f_globals["__name__"]
    keywords = {"requirement": requirement, "changes_behavior": changes_behavior}
    return type(
        name, (base,), {"__module__": module}, rule=rule, why=why, min_length=min_length, shows_as=shows_as, **keywords
    )


def _checked_levels(levels, where):
    for rule, level in levels.items():
        if not isinstance(level, Requirement):
            raise TypeError(
                _("%(where)s gives %(rule)r the level %(level)r - write a Requirement, e.g. Requirement.OPTIONAL.")
                % {"where": where, "rule": rule, "level": level}
            )
    return levels


def exemption_requirements(levels):
    """
    Sets the level of each rule named, `{rule: Requirement}`, replacing what an earlier call set - for a
    project without Django, before what declares exemptions is imported. With Django, the
    `ISIK_EXEMPTIONS` setting says the same, and a rule named here wins over it.

        exemption_requirements({"settings.question": Requirement.OPTIONAL, "foo.bar": Requirement.OFF})
    """
    _levels.clear()
    _levels.update(_checked_levels(dict(levels), "exemption_requirements()"))


def _django_levels():
    try:
        from django.conf import settings
    except ImportError:
        return {}
    if not settings.configured:
        return {}
    return _checked_levels(getattr(settings, "ISIK_EXEMPTIONS", {}), "ISIK_EXEMPTIONS")


def requirement_of(exemption_type):
    """
    The level `exemption_type`'s rule is held to: the project's, for its rule or a rule it's a kind of
    (`NoReauthentication` under `isik.request-policy`), else the level its owner gave it.
    """
    for source in (_levels, _django_levels()):
        for cls in exemption_type.__mro__:
            rule = cls.__dict__.get("rule")
            if rule is not None and rule in source:
                level = source[rule]
                if level is Requirement.OFF and exemption_type.changes_behavior:
                    raise TypeError(
                        _(
                            "%(rule)s can't be Requirement.OFF: its exemption changes what code does rather "
                            "than answering a check. Make it Requirement.OPTIONAL instead."
                        )
                        % {"rule": rule}
                    )
                return level
    return exemption_type.requirement


def enforced(exemption_type):
    """False when `exemption_type`'s rule is `Requirement.OFF` - a check of it then asks nothing."""
    return requirement_of(exemption_type) is not Requirement.OFF


def opted_out(value, exemption_type, *, plain):
    """
    The exemption `value` declares, for a check or an API to act on - None when it declares none:

    - an `exemption_type` is itself;
    - `plain` - the value the slot reads as "opt out", `True` for `exempt_from_registry`, `False` for a
      setting's `question=` - is, unless the rule is strict, an `exemption_type` with no reason, listed
      with the rest;
    - anything else is None, for the caller to refuse or ignore.

        opted_out(cls.exempt_from_registry, ViewSetRegistryExemption, plain=True)
    """
    if isinstance(value, exemption_type):
        return value
    if value is plain and requirement_of(exemption_type) is not Requirement.STRICT:
        return exemption_type()
    return None


def makes_exemption(exemption_type):
    """
    Marks a function that makes `exemption_type` exemptions for its caller, so each is recorded where
    the function was called rather than inside it, and `unseen_exemption_calls()` counts calls to it:

        @makes_exemption(NotAtomicReason)
        def not_atomic(reason):
            reason = NotAtomicReason(reason=reason)
            ...

        @not_atomic("streams to storage the whole time")   # recorded here
        def upload(request): ...

    Under other decorators, it marks every function down the `__wrapped__` chain.
    """
    if not (isinstance(exemption_type, type) and issubclass(exemption_type, Exemption) and exemption_type.rule):
        raise TypeError(_("makes_exemption() takes an exemption type, not %(value)r.") % {"value": exemption_type})

    def mark(function):
        wrapped = function
        while wrapped is not None:
            _makers[wrapped.__code__] = function.__name__
            wrapped = getattr(wrapped, "__wrapped__", None)
        return function

    return mark


def makes_own_exemptions(function):
    """
    Marks a library function whose exemptions are the library's own, not its caller's. Each is recorded
    at the function's own line and flagged `library`, so a project's listing and budgets leave it out:

        @makes_own_exemptions
        def teach_django_its_validators():
            for validator, why in DJANGO_VALIDATORS_WITHOUT_ONE.items():
                validator.no_database_form = NoDatabaseForm(reason=why)

    Without it, an exemption made while the project loads - from an `AppConfig.ready()`, say - is
    recorded at the nearest frame outside the libraries, `manage.py`, and counted as the project's.

    Under other decorators it marks only the innermost function, whose body makes them: a wrapper's
    code can be shared by every function it wraps, and marking it would claim theirs too.
    """
    _own.add(inspect.unwrap(function).__code__)
    return function


def exemption_types():
    """`{rule: type}` for every exemption type defined so far."""
    return dict(sorted(_types.items()))


def declared_exemptions(rule=None, *, include_library=False):
    """
    Every exemption made so far, in the order made - or only `rule`'s. One made in a function exists
    once the function runs; `unseen_exemption_calls()` finds those that haven't. A library's own
    (`makes_own_exemptions()`) are left out unless `include_library`.
    """
    return [
        exemption
        for exemption in _declared
        if (rule is None or exemption.rule == rule) and (include_library or not exemption.library)
    ]


def _known_rule(rule):
    if rule not in _types:
        raise LookupError(_("No exemption type exempts from %(rule)r.") % {"rule": rule})


def assert_exemption_budget(rule, *, at_most):
    """
    Fails unless `rule` has at most `at_most` exemptions - so adding one is a change to a test, made in
    review, not a silent one. Call it once everything that declares them is imported. A library's own
    don't count.
    """
    _known_rule(rule)
    found = declared_exemptions(rule)
    if len(found) > at_most:
        where = "\n".join(f"  {exemption.file}:{exemption.line} {exemption.reason}" for exemption in found)
        raise AssertionError(
            _("%(rule)s has %(count)d exemptions, over its budget of %(budget)d:\n%(where)s")
            % {"rule": rule, "count": len(found), "budget": at_most, "where": where}
        )


# Test code makes exemptions to check they're refused or listed, and the listing never runs it.
TEST_CODE = ("tests", "test_*.py", "*_test.py", "conftest.py")


def _name(node):
    """The name an expression is spelled with: `X` and `module.X` are both `X`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _source_files(paths, exclude):
    """(file, tree, {alias: imported name}) for each `.py` file under `paths` that isn't excluded or a migration."""
    for root in map(Path, paths):
        for path in sorted(root.rglob("*.py")):
            parts = path.relative_to(root).parts
            if "migrations" in parts or any(fnmatch(part, pattern) for part in parts for pattern in exclude):
                continue
            try:
                tree = ast.parse(path.read_bytes())
            except (SyntaxError, ValueError):
                continue
            aliases = {
                alias.asname: alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                for alias in node.names
                if alias.asname
            }
            yield str(path.resolve()), tree, aliases


def _module_file(module):
    found = getattr(sys.modules.get(module), "__file__", None)
    return str(Path(found).resolve()) if found else None


class _Scan:
    """What `paths` declare and call, read without importing any of it."""

    def __init__(self, paths, exclude):
        self.files = list(_source_files(paths, exclude))
        self.types = {cls.__name__ for cls in _types.values()}
        self.makers = set(_makers.values())
        self.declared_types = []
        # Names made by exemption_class() count as types before any class can subclass them.
        for file, tree, aliases in self.files:
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    if aliases.get(_name(node.value.func), _name(node.value.func)) == "exemption_class":
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                self._declare(file, node.lineno, target.id)
                elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    for decorator in node.decorator_list:
                        called = _name(decorator.func) if isinstance(decorator, ast.Call) else None
                        if aliases.get(called, called) == "makes_exemption":
                            self.makers.add(node.name)
        # A class is a type when one of its bases is - which may be declared later, or in another file.
        # Each class is filed under its bases' names once, then reached from the types it derives from.
        by_base = {}
        for file, tree, aliases in self.files:
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        by_base.setdefault(aliases.get(_name(base), _name(base)), []).append((file, node))
        # A for over the list it appends to - no step that consumes it, so nothing that can leave it spinning.
        names = [*self.types, "Exemption"]
        reached = set()
        for name in names:
            for file, node in by_base.get(name, ()):
                if (file, node.lineno) not in reached:
                    reached.add((file, node.lineno))
                    self._declare(file, node.lineno, node.name)
                    names.append(node.name)

    def _declare(self, file, line, name):
        self.types.add(name)
        self.declared_types.append((file, line, name))

    def unimported_types(self):
        registered = {(_module_file(cls.__module__), cls.__name__) for cls in _types.values()}
        return sorted((file, line, name) for file, line, name in self.declared_types if (file, name) not in registered)

    def unseen_calls(self):
        seen = {(exemption.file, exemption.line) for exemption in _declared}
        unseen = []
        for file, tree, aliases in self.files:
            # What a maker makes is recorded at the maker's caller, never inside its own body.
            in_makers = {
                id(inner)
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in self.makers
                for inner in ast.walk(node)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or id(node) in in_makers or (file, node.lineno) in seen:
                    continue
                name = aliases.get(_name(node.func), _name(node.func))
                gives_reason = any(keyword.arg == "reason" for keyword in node.keywords)
                if (name in self.types and gives_reason) or (name in self.makers and (gives_reason or node.args)):
                    unseen.append((file, node.lineno, name))
        return sorted(unseen)


def unseen_exemption_calls(paths, exclude=TEST_CODE):
    """
    `[(file, line, name)]` for every call under `paths` that makes an exemption no exemption was made from
    - one inside a function that hasn't run, say. A call is to an exemption type with `reason=`, or to a
    `makes_exemption()` function; types and those functions count whether they're imported or only
    declared in these files, and `from ... import X as Y` is followed. `name` is the type's or
    function's own.

    Skipped: migrations, which replay exemptions rather than make them, and any file or directory whose
    name matches a pattern in `exclude` - test code by default (`TEST_CODE`), which makes exemptions on
    purpose and never runs while a project loads.
    """
    return _Scan(paths, exclude).unseen_calls()


def unimported_exemption_types(paths, exclude=TEST_CODE):
    """
    `[(file, line, name)]` for every exemption type declared under `paths` - a subclass of `Exemption` or
    of another type, or an `exemption_class()` - that isn't defined: its module was never imported, so
    its rule is missing from `exemption_types()`. Skips what `unseen_exemption_calls()` skips.
    """
    return _Scan(paths, exclude).unimported_types()
