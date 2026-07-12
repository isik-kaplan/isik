def is_dunder(name):
    """True for __dunder__-shaped names."""
    return name[:2] == name[-2:] == "__" and name[2:3] != "_" and name[-3:-2] != "_" and len(name) > 4


class transform(type):
    """
    A metaclass that runs every class-body attribute - and any attribute assigned to the
    class afterward - through a pair of hooks the consuming class defines on itself:

    - __checks__(key, value, classdict) -> bool: whether `value` should be transformed.
      Defaults to "transform everything".
    - __transform__(key, value, classdict): returns the replacement for `value`.
      Defaults to the identity function.

    Both hooks are looked up on the class's own body only - they are not inherited from a
    base class. `classdict` is the class's own namespace at the time of the call (its
    original body at construction time, `vars(cls)` for a later assignment).

    Set `__abstract__ = True` in the class body to make the class itself impossible to
    instantiate - useful when the class is meant to be a namespace/registry rather than a
    real object, and shouldn't be constructed by mistake.

    Example:
        class Doubled(metaclass=transform):
            __checks__ = staticmethod(lambda key, value, classdict: isinstance(value, int))
            __transform__ = staticmethod(lambda key, value, classdict: value * 2)

            one = 1
            two = 2

        Doubled.one  # 2
        Doubled.two  # 4
        Doubled.two = 10
        Doubled.two  # 20
    """

    @staticmethod
    def __noop_transform(key, value, classdict):
        return value

    @staticmethod
    def __noop_checks(key, value, classdict):
        return True

    @staticmethod
    def __raise_on_new(name):
        def __new__(cls, *a, **kw):
            raise TypeError(f"{name} can not be instantiated.")

        return __new__

    def __new__(mcs, name, bases, classdict):
        transform_ = classdict.get("__transform__", mcs.__noop_transform)
        checks = classdict.get("__checks__", mcs.__noop_checks)
        new_classdict = {
            key: transform_(key, value, classdict) if checks(key, value, classdict) else value
            for key, value in classdict.items()
        }
        if classdict.get("__abstract__", False):
            new_classdict["__new__"] = mcs.__raise_on_new(name)
        return super().__new__(mcs, name, bases, new_classdict)

    def __setattr__(cls, key, value):
        classdict = vars(cls)
        transform_ = classdict.get("__transform__", transform.__noop_transform)
        checks = classdict.get("__checks__", transform.__noop_checks)
        if checks(key, value, classdict):
            value = transform_(key, value, classdict)
        super().__setattr__(key, value)
