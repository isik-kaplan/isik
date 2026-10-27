"""
What a maker needs of a model a project wrote itself - `model=` on `votes()`/`comments()`/..., and
`tag_model=`/`through_model=` on `tags()` - checked once the model is registered, with every problem
reported at once. A relation is compared by label, so it holds before Django has resolved it.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from django.db.models.fields.related import resolve_relation
from django.db.models.utils import make_model_tuple

from isik._internal.translation import gettext as _


def label(model_ref):
    """`app_label.modelname` for a model class or an `"app_label.Model"` string."""
    return ".".join(make_model_tuple(model_ref))


def points_at(field, target):
    """Whether `field` is a foreign key to `target` - a class or an `"app_label.Model"` string - however
    the key names its model: a class, `"self"`, `"Model"` or `"app_label.Model"`."""
    return isinstance(field, models.ForeignKey) and make_model_tuple(
        resolve_relation(field.model, field.remote_field.model)
    ) == make_model_tuple(target)


@dataclass(frozen=True)
class Need:
    """A field a maker reads by `name` - `what` says what it must be, `fits(field)` checks it."""

    name: str
    what: str
    fits: Callable


def foreign_key(name, target, what):
    return Need(name, what, lambda found: points_at(found, target))


def kind(name, kinds, what):
    return Need(name, what, lambda found: isinstance(found, kinds))


@dataclass(frozen=True)
class Contract:
    """What `maker` reads of a model: the fields it needs, the ones that switch something on when
    present, and the field sets that must be unique together."""

    maker: str
    needs: tuple = ()
    optional: tuple = ()
    unique: tuple = ()

    def problems(self, model):
        found = []
        for need in self.needs:
            try:
                present = model._meta.get_field(need.name)
            except FieldDoesNotExist:
                found.append(
                    _("it has no %(field)s - %(maker)s needs %(what)s named %(field)s")
                    % {"field": need.name, "maker": self.maker, "what": need.what}
                )
                continue
            found.extend(self._mismatch(need, present))
        for need in self.optional:
            try:
                present = model._meta.get_field(need.name)
            except FieldDoesNotExist:
                continue
            found.extend(self._mismatch(need, present))
        for names in self.unique:
            if not unique_on(model, names):
                found.append(
                    _("it needs a unique constraint on (%(fields)s) - %(maker)s relies on it")
                    % {"fields": ", ".join(names), "maker": self.maker}
                )
        return found

    def _mismatch(self, need, present):
        if need.fits(present):
            return []
        return [_("its %(field)s must be %(what)s") % {"field": need.name, "what": need.what}]

    def check(self, model):
        """Raises `ImproperlyConfigured` with every problem `model` has, if it has any."""
        found = self.problems(model)
        if found:
            raise ImproperlyConfigured(
                _("%(model)s can't be %(maker)s's model: %(problems)s.")
                % {"model": model._meta.label, "maker": self.maker, "problems": "; ".join(found)}
            )


def unique_on(model, names):
    """Whether `names` are unique together on `model` - by a plain `UniqueConstraint`, `unique_together`,
    or, for one name, the field's own `unique`."""
    wanted = set(names)
    for constraint in model._meta.constraints:
        if (
            isinstance(constraint, models.UniqueConstraint)
            and constraint.condition is None
            and set(constraint.fields) == wanted
        ):
            return True
    if any(set(together) == wanted for together in model._meta.unique_together):
        return True
    return len(names) == 1 and model._meta.get_field(names[0]).unique


def the_foreign_key_to(model, target, name, maker):
    """The foreign key on `model` to `target`: the one named `name` if given, else the only one."""
    where = {"model": model._meta.label, "maker": maker, "target": label(target)}
    if name is not None:
        found = {field.name: field for field in model._meta.fields}.get(name)
        if not points_at(found, target):
            raise ImproperlyConfigured(
                _("%(model)s can't be %(maker)s's model: its %(field)s must be a ForeignKey to %(target)s.")
                % {**where, "field": name}
            )
        return found
    keys = [candidate for candidate in model._meta.fields if points_at(candidate, target)]
    if len(keys) != 1:
        raise ImproperlyConfigured(
            _(
                "%(model)s can't be %(maker)s's model: it needs exactly one ForeignKey to %(target)s, or "
                "target_name= naming the one to use - it has %(count)d."
            )
            % {**where, "count": len(keys)}
        )
    return keys[0]
