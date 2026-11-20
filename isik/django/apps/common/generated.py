"""
The models isik generates - a tracked model's event model, a maker's (`tags()`, `votes()`, ...), a
version's lock proxy - marked so a project needn't guess them by their names:

    Widget.pgh_event_model.isik_generated   # "event"
    generated_models("event")               # every event model isik made

`enum_name_overrides()` (`isik.django.drf.spectacular`) names their enums from it.
"""

import sys


# {model label: (model, kind)} - by label, so a model defined again (a test run twice, an autoreload)
# replaces itself rather than being listed twice.
_generated = {}

KINDS = ("event", "maker", "lock")


def mark_generated(model, kind):
    """
    Records `model` as one isik made, of `kind` - one of `KINDS` - and returns it. It's set on the module it
    names as its own, so what imports a model by module and name - the shell's automatic imports, a pickle -
    finds it there; a name of the module's own is never replaced, only an earlier copy of a generated one.
    """
    model.isik_generated = kind
    _generated[model._meta.label] = (model, kind)
    module = sys.modules.get(model.__module__)
    held = getattr(module, model.__name__, None)
    if module is not None and (held is None or getattr(held, "isik_generated", None) is not None):
        setattr(module, model.__name__, model)
    return model


def generated_models(kind=None):
    """Every model isik generated, in the order made - or only `kind`'s."""
    return [model for model, made in _generated.values() if kind is None or made == kind]
