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
import os
import re
import sys
import sysconfig
from fnmatch import fnmatch
from pathlib import Path

from isik._internal.translation import gettext as _
from isik.common.utils.sentinel import Sentinel


NOT_GIVEN = Sentinel("EXEMPTION_NOT_GIVEN")
RULE_PATTERN = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)*")
DEFAULT_MIN_LENGTH = 40

# {rule: type}, every exemption made so far in the order made, and {code: name} for each function that
# makes exemptions for its caller (`makes_exemption()`).
_types = {}
_declared = []
_makers = {}

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
    and not in a function that makes exemptions for its caller (`makes_exemption()`).
    """
    frame = frame or sys._getframe()
    in_libraries = []
    while frame is not None:
        file = str(Path(frame.f_code.co_filename).resolve())
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

    def __init_subclass__(cls, *, rule=None, why=None, min_length=None, shows_as=NOT_GIVEN, **kwargs):
        super().__init_subclass__(**kwargs)
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
        if "reason" not in kwargs:
            raise TypeError(_("%(type)s needs reason=.") % {"type": cls.__name__})
        self = cls._made(cls._cleaned(kwargs["reason"]))
        frame, file = _call_site()
        if frame is not None:
            self.file, self.line = file, frame.f_lineno
            if not _in_a_migration(frame):
                _declared.append(self)
        return self

    @classmethod
    def _cleaned(cls, reason):
        if not isinstance(reason, str):
            raise TypeError(
                _("%(type)s's reason must be text, not %(value)r.") % {"type": cls.__name__, "value": reason}
            )
        reason = " ".join(reason.split())
        if len(reason) < cls.min_length:
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
        return self

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


def exemption_class(name, *, rule, why, min_length=None, shows_as=NOT_GIVEN, base=Exemption, module=None):
    """
    `Exemption` subclassed in one line, in the caller's module - so migrations can import it by name:

        NoHelpText = exemption_class(
            "NoHelpText",
            rule="schema-docs.help-text",
            why="Every field should say what it holds in help_text.",
            shows_as="",
        )

    `module=` names the module it's importable from instead, when it's assigned somewhere else.
    """
    if module is None:
        frame, _file = _call_site()
        if frame is None:
            raise TypeError(
                _("exemption_class() can't tell which module %(name)s is in - pass module=.") % {"name": name}
            )
        module = frame.f_globals["__name__"]
    return type(name, (base,), {"__module__": module}, rule=rule, why=why, min_length=min_length, shows_as=shows_as)


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


def exemption_types():
    """`{rule: type}` for every exemption type defined so far."""
    return dict(sorted(_types.items()))


def declared_exemptions(rule=None):
    """
    Every exemption made so far, in the order made - or only `rule`'s. One made in a function exists
    once the function runs; `unseen_exemption_calls()` finds those that haven't.
    """
    return [exemption for exemption in _declared if rule is None or exemption.rule == rule]


def _known_rule(rule):
    if rule not in _types:
        raise LookupError(_("No exemption type exempts from %(rule)r.") % {"rule": rule})


def assert_exemption_budget(rule, *, at_most):
    """
    Fails unless `rule` has at most `at_most` exemptions - so adding one is a change to a test, made in
    review, not a silent one. Call it once everything that declares them is imported.
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
        classes = set()
        while found := self._subclasses(classes):
            for file, node in found:
                classes.add((file, node.lineno))
                self._declare(file, node.lineno, node.name)

    def _subclasses(self, classes):
        """Classes not yet in `classes` with a base that's a type - `Exemption` itself, or one found so far."""
        return [
            (file, node)
            for file, tree, aliases in self.files
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and (file, node.lineno) not in classes
            and any(aliases.get(_name(base), _name(base)) in self.types | {"Exemption"} for base in node.bases)
        ]

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
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or (file, node.lineno) in seen:
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
