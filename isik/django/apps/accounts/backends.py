"""
What deleting an account does - `ISIK['DJANGO']['ACCOUNTS']['DELETION_BACKEND']`:

- `Erase` (isik's, unless set) - the person's row goes, with everything that cascades from it and every registered
  model's rows of theirs, and the history of each of them.
- `Anonymize` - the person's row stays, a tombstone: the fields the user model's registration names set, the password
  unusable, inactive. Registered `delete=True` rows go with their history; the rest stay with their `anonymize`
  fields set, in their history too. Who did what, and when, stays - as the tombstone.

A backend of the project's own is a class with `delete(user)`, called inside the deleting transaction.
"""

import uuid

import pgtrigger
from django.contrib.auth import get_user_model
from django.db import router
from django.db.models.deletion import Collector

from isik.django.apps.accounts.erasures import erasures


def _doomed(objects):
    """{model: {pk, ...}} - `objects`, and every row their delete cascades to."""
    if not objects:
        return {}
    collector = Collector(using=router.db_for_write(type(objects[0])))
    collector.collect(objects)
    doomed = {}
    for model, instances in collector.data.items():
        doomed.setdefault(model, set()).update(instance.pk for instance in instances)
    for queryset in collector.fast_deletes:
        doomed.setdefault(queryset.model, set()).update(queryset.values_list("pk", flat=True))
    return doomed


def _events_of(model):
    return list(getattr(model, "pgh_event_models", {}).values())


def _erase_history(doomed):
    """Deletes the history of every row of `doomed`. Every trigger stands aside: an event table is append-only."""
    with pgtrigger.ignore():
        for model, pks in doomed.items():
            for event_model in _events_of(model):
                event_model._default_manager.filter(pgh_obj_id__in=pks).delete()


def _delete(rows):
    """Deletes `rows`, what cascades from them, and the history of all of it."""
    doomed = _doomed(list(rows))
    rows.delete()
    _erase_history(doomed)


def _set(row, fields):
    """`fields` for `row` - each a value, or f(row) for one of its own."""
    return {name: value(row) if callable(value) else value for name, value in fields.items()}


def _anonymize(rows, fields):
    """Sets `fields` on each of `rows`, and on its history's copies of them."""
    for row in list(rows):
        values = _set(row, fields)
        type(row)._default_manager.filter(pk=row.pk).update(**values)
        _scrub_history(type(row), row.pk, values)


def _scrub_history(model, pk, values):
    with pgtrigger.ignore():
        for event_model in _events_of(model):
            held = {name: value for name, value in values.items() if name in _field_names(event_model)}
            if held:
                event_model._default_manager.filter(pgh_obj_id=pk).update(**held)


def _field_names(model):
    return {each.name for each in model._meta.concrete_fields}


def _others(user):
    """Every registered model but the user's own."""
    label = type(user)._meta.label
    return [registered for name, registered in erasures.items() if name != label]


class Erase:
    """The person's row goes, with what cascades from it and every registered model's rows of theirs - and their
    history."""

    def delete(self, user):
        for registered in _others(user):
            _delete(registered.rows_of(user))
        _delete(get_user_model()._default_manager.filter(pk=user.pk))


class Anonymize:
    """The person's row stays as a tombstone; registered rows are deleted, or kept with their fields set."""

    def delete(self, user):
        for registered in _others(user):
            rows = registered.rows_of(user)
            if registered.delete:
                _delete(rows)
            elif registered.anonymize:
                _anonymize(rows, registered.anonymize)
        own = erasures[type(user)._meta.label]
        values = _set(user, own.anonymize)
        for name, value in values.items():
            setattr(user, name, value)
        user.set_unusable_password()
        user.deletes_at = None
        user.session_epoch = uuid.uuid4()
        # Every user model has is_active - AbstractBaseUser's True, unless it's a field of its own.
        user.is_active = False
        user.save()
        _scrub_history(type(user), user.pk, values)
