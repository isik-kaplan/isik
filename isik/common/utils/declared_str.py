"""
DeclaredStr + text()/attribute() - a `str` whose value, validation and carried attributes are declared
on the class, rather than hand-written in a `__new__` each time.

    class Exemption(DeclaredStr):
        reason = text(min_length=40)

    class NoComment(DeclaredStr, displays_as=""):
        reason = text(min_length=40, max_length=300)

    class PermissionName(DeclaredStr):
        name = text(pattern=r"[a-z_]+:[a-z_]+:[a-z_]+")
        label = attribute(str)
        delegatable = attribute(bool, default=False)

    Exemption("the registry would reject a second viewset over this model")   # truthy, reads as its reason
    NoComment("written by the trigger, never by hand")   # == "" to anything reading a str
    PermissionName("org:invite:member", label="Invite members")

The first `text()` a class declares is its positional argument, and the str's value - unless
`displays_as=` says what the str is instead, which is how a sentinel can be empty to Django while
carrying a reason for a check to read. Everything declared is kept on the instance under its own name.
Lengths and patterns are per class; a project's own base class sets them once for its subclasses.
"""

import re

from isik._internal.translation import gettext as _
from isik.common.utils.sentinel import Sentinel


MISSING = Sentinel("DECLARED_STR_MISSING")


class Declaration:
    """What a `DeclaredStr` carries under one name - see `DeclaredText`/`DeclaredAttribute`."""

    default = MISSING

    def clean(self, owner, name, value):
        """The value to keep for `name`, or a `TypeError`/`ValueError` naming `owner` and why."""
        raise NotImplementedError


class DeclaredText(Declaration):
    """
    Text, with its whitespace collapsed to single spaces unless `collapse_whitespace=False`, then held
    to `min_length`/`max_length` and to `pattern` (matched in full) - usually spelled `text()`.
    """

    def __init__(self, min_length=None, max_length=None, pattern=None, collapse_whitespace=True):
        self.min_length = min_length
        self.max_length = max_length
        self.pattern = re.compile(pattern) if isinstance(pattern, str) else pattern
        self.collapse_whitespace = collapse_whitespace

    def clean(self, owner, name, value):
        if not isinstance(value, str):
            raise TypeError(
                _("%(owner)s's %(name)s must be text, not %(value)r.")
                % {"owner": owner.__name__, "name": name, "value": value}
            )
        if self.collapse_whitespace:
            value = " ".join(value.split())
        params = {"owner": owner.__name__, "name": name, "value": value}
        if self.min_length is not None and len(value) < self.min_length:
            raise ValueError(
                _("%(owner)s needs a %(name)s of at least %(length)d characters, not %(value)r.")
                % {**params, "length": self.min_length}
            )
        if self.max_length is not None and len(value) > self.max_length:
            raise ValueError(
                _("%(owner)s needs a %(name)s of at most %(length)d characters, not %(value)r.")
                % {**params, "length": self.max_length}
            )
        if self.pattern is not None and not self.pattern.fullmatch(value):
            raise ValueError(
                _("%(owner)s's %(name)s must match %(pattern)s, and %(value)r doesn't.")
                % {**params, "pattern": self.pattern.pattern}
            )
        return value


class DeclaredAttribute(Declaration):
    """A keyword argument kept as-is, checked against `type` if given - usually spelled `attribute()`."""

    def __init__(self, type=None, default=MISSING):
        self.type = type
        self.default = default

    def clean(self, owner, name, value):
        if self.type is not None and not isinstance(value, self.type):
            raise TypeError(
                _("%(owner)s's %(name)s must be a %(type)s, not %(value)r.")
                % {"owner": owner.__name__, "name": name, "type": self.type.__name__, "value": value}
            )
        return value


def text(min_length=None, max_length=None, pattern=None, collapse_whitespace=True):
    """Declares text a `DeclaredStr` carries - see `DeclaredText`."""
    return DeclaredText(min_length, max_length, pattern, collapse_whitespace)


def attribute(type=None, default=MISSING):
    """Declares a keyword attribute a `DeclaredStr` carries - see `DeclaredAttribute`."""
    return DeclaredAttribute(type, default)


class DeclaredStr(str):
    """
    A `str` built from its declarations - see the module docstring. Equal to, and hashed as, the str
    it displays as; copied, pickled and written into migrations (`deconstruct()`) as itself.
    """

    declarations = {}
    positional = None
    displays_as = None

    def __init_subclass__(cls, displays_as=MISSING, **kwargs):
        super().__init_subclass__(**kwargs)
        if displays_as is not MISSING:
            cls.displays_as = displays_as
        cls.declarations = {
            name: value
            for klass in reversed(cls.__mro__)
            for name, value in vars(klass).items()
            if isinstance(value, Declaration)
        }
        texts = [name for name, value in cls.declarations.items() if isinstance(value, DeclaredText)]
        cls.positional = texts[0] if texts else None
        if cls.positional is None and cls.displays_as is None:
            raise TypeError(
                _("%(cls)s declares no text() and no displays_as=, so it has nothing to be.") % {"cls": cls.__name__}
            )

    def __new__(cls, *args, **kwargs):
        if len(args) > (1 if cls.positional else 0):
            raise TypeError(
                _("%(cls)s takes %(count)d positional argument(s), not %(given)d.")
                % {"cls": cls.__name__, "count": 1 if cls.positional else 0, "given": len(args)}
            )
        if args:
            if cls.positional in kwargs:
                raise TypeError(_("%(cls)s got %(name)s twice.") % {"cls": cls.__name__, "name": cls.positional})
            kwargs = {cls.positional: args[0], **kwargs}
        unknown = sorted(set(kwargs) - set(cls.declarations))
        if unknown:
            raise TypeError(_("%(cls)s declares no %(names)s.") % {"cls": cls.__name__, "names": ", ".join(unknown)})
        values = {}
        for name, declaration in cls.declarations.items():
            if name in kwargs:
                values[name] = declaration.clean(cls, name, kwargs[name])
            elif declaration.default is not MISSING:
                values[name] = declaration.default
            else:
                raise TypeError(_("%(cls)s needs a %(name)s.") % {"cls": cls.__name__, "name": name})
        self = super().__new__(cls, values[cls.positional] if cls.displays_as is None else cls.displays_as)
        self.__dict__.update(values)
        return self

    def _arguments(self):
        args = (getattr(self, self.positional),) if self.positional else ()
        kwargs = {
            name: getattr(self, name)
            for name, declaration in self.declarations.items()
            if name != self.positional and getattr(self, name) != declaration.default
        }
        return args, kwargs

    def __getnewargs_ex__(self):
        return self._arguments()

    def deconstruct(self):
        """How Django's migration writer spells it: as itself, not as the str it equals."""
        args, kwargs = self._arguments()
        return f"{type(self).__module__}.{type(self).__qualname__}", args, kwargs

    def __repr__(self):
        args, kwargs = self._arguments()
        written = [repr(arg) for arg in args] + [f"{name}={value!r}" for name, value in kwargs.items()]
        return f"{type(self).__name__}({', '.join(written)})"
