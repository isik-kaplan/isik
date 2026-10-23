"""
The models isik generates - a tracked model's event model, a maker's (`tags()`, `votes()`, ...), a
version's lock proxy - marked so a project needn't guess them by their names:

    Widget.pgh_event_model.isik_generated   # "event"
    generated_models("event")               # every event model isik made

`enum_name_overrides()` (`isik.django.drf.spectacular`) names their enums from it.
"""

# {model label: (model, kind)} - by label, so a model defined again (a test run twice, an autoreload)
# replaces itself rather than being listed twice.
_generated = {}

KINDS = ("event", "maker", "lock")


def mark_generated(model, kind):
    """Records `model` as one isik made, of `kind` - one of `KINDS` - and returns it."""
    model.isik_generated = kind
    _generated[model._meta.label] = (model, kind)
    return model


def generated_models(kind=None):
    """Every model isik generated, in the order made - or only `kind`'s."""
    return [model for model, made in _generated.values() if kind is None or made == kind]
