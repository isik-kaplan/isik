"""redeclare() and if_unset() - a library's field, declared again as the project's own, changed only where asked."""

import copy
from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.fields.related import RelatedField

from isik._internal.translation import gettext as _


@dataclass(frozen=True)
class IfUnset:
    """What `if_unset(value)` gives: `value`, for `redeclare()` to use only where the library set none."""

    value: object


def if_unset(value):
    """
    `value` for one of `redeclare()`'s arguments, used only where the library's field leaves that argument at
    Django's default - its own text, where it wrote one, stays.
    """
    return IfUnset(value)


def redeclare(field, **changes):
    """
    The field an abstract model declares - read off it, `AbstractUser.first_name`, or given itself - built
    again from its own definition with `changes` made, to declare on a model inheriting it:

        class User(AbstractUser):
            first_name = redeclare(AbstractUser.first_name, max_length=200, help_text="What the person is called.")
            is_staff = redeclare(AbstractUser.is_staff, help_text=if_unset("..."), db_comment="...")

    Every argument the library gave it is kept, so nothing changes but what `changes` names: any argument its
    constructor takes, which a plain value sets and `if_unset(value)` sets only where the library set none. It
    is declared again, so it is the project's own - `isik.E010` checks it whether or not fields inherited from
    a library are checked. A field reached through the model inheriting it from further up - `AbstractUser`'s
    `password`, which `AbstractBaseUser` declares - is the same field.

    A concrete model's field is refused: its column is in that model's table, not one a model inheriting it
    could declare it in. So is a call changing nothing.
    """
    field = getattr(field, "field", field)
    if not isinstance(field, models.Field):
        raise TypeError(_("redeclare() takes a model's field, not %(value)r.") % {"value": field})
    model = field.model
    if not model._meta.abstract:
        raise ImproperlyConfigured(
            _(
                "redeclare(%(model)s.%(field)s): %(model)s is a concrete model, whose own table holds %(field)s - "
                "redeclare() takes a field an abstract model declares."
            )
            % {"model": model.__name__, "field": field.name}
        )
    if not changes:
        raise ImproperlyConfigured(
            _("redeclare(%(model)s.%(field)s) changes nothing - inheriting it declares the same field.")
            % {"model": model.__name__, "field": field.name}
        )
    # A key's deconstruct() asks the app registry whether its model is swappable, which it can't answer while
    # models are still being declared - so it's read off a copy that doesn't ask, and the key built keeps its own.
    unasking = copy.copy(field)
    unasking.__dict__ |= {"swappable": False}
    _name, _path, args, kwargs = unasking.deconstruct()
    if isinstance(field, RelatedField):
        kwargs["swappable"] = field.swappable
    for argument, value in changes.items():
        if not isinstance(value, IfUnset):
            kwargs[argument] = value
        elif argument not in kwargs:
            kwargs[argument] = value.value
    return field.__class__(*args, **kwargs)
