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
from pathlib import Path

from isik._internal.translation import gettext as _
from isik.common.utils.sentinel import Sentinel


NOT_GIVEN = Sentinel("EXEMPTION_NOT_GIVEN")
RULE_PATTERN = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)*")
DEFAULT_MIN_LENGTH = 40

# {rule: type}, and every exemption made so far, in the order made.
_types = {}
_declared = []

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
    """(frame, file) of the code that made an exemption: the nearest frame outside isik and the libraries."""
    frame = frame or sys._getframe()
    in_libraries = []
    while frame is not None:
        file = str(Path(frame.f_code.co_filename).resolve())
        if not file.startswith(_ISIK):
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


def _called_name(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def unseen_exemption_calls(paths):
    """
    `[(file, line, type name)]` for every `<ExemptionType>(reason=...)` call in the `.py` files under
    `paths` that no exemption was made from - one inside a function that hasn't run, say. Migrations
    are skipped: they replay exemptions rather than make them.
    """
    names = {cls.__name__ for cls in _types.values()}
    seen = {(exemption.file, exemption.line) for exemption in _declared}
    unseen = []
    for root in paths:
        for path in sorted(Path(root).rglob("*.py")):
            if "migrations" in path.parts:
                continue
            file = str(path.resolve())
            try:
                tree = ast.parse(path.read_bytes())
            except (SyntaxError, ValueError):
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and _called_name(node) in names
                    and any(keyword.arg == "reason" for keyword in node.keywords)
                    and (file, node.lineno) not in seen
                ):
                    unseen.append((file, node.lineno, _called_name(node)))
    return sorted(unseen)
