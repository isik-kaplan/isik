"""
The features app's system checks - `isik.E017` (`Check.FEATURES`) for a setting or declaration that can't work,
and its share of `isik.E016` (`Check.LABELS`): a feature or setting that can't say what it is.
"""

import importlib.util

import pghistory.trigger
import pgtrigger
from django.core.checks import Error
from django.db import models

from isik._internal.translation import gettext as _
from isik.django.apps.common.checks import Check, isik_check
from isik.django.apps.common.db.history import APPEND_ONLY_TRIGGERS
from isik.django.apps.features import conf, declarations
from isik.django.apps.features.bases import AbstractOverrides
from isik.django.apps.features.swapping import SETTABLE, get_grant_model, get_holder_model, get_overrides_model
from isik.django.conf import swappable_keys


def setting_problems():
    """The problems with `ISIK['DJANGO']['FEATURES']` and the overrides model."""
    problems = []
    unknown = sorted(set(conf.configured()) - set(conf.DEFAULTS) - swappable_keys(SETTABLE, "FEATURES"))
    if unknown:
        problems.append(
            _("ISIK['DJANGO']['FEATURES'] has keys it doesn't know: %(keys)s.") % {"keys": ", ".join(unknown)}
        )
    sweep = conf.setting("SWEEP")
    if sweep not in conf.SWEEPS:
        problems.append(
            _("ISIK['DJANGO']['FEATURES']['SWEEP'] is %(sweep)r - it's one of %(sweeps)s.")
            % {"sweep": sweep, "sweeps": ", ".join(conf.SWEEPS)}
        )
    if sweep == "next_boundary" and importlib.util.find_spec("celery") is None:
        problems.append(
            _(
                "ISIK['DJANGO']['FEATURES']['SWEEP'] is next_boundary, which schedules each run with Celery - "
                "install it."
            )
        )
    problems += _every_feature_problems(conf.setting("HOLDS_EVERY_FEATURE"))
    overrides = get_overrides_model()
    if declarations.settings and overrides is None:
        problems.append(
            _(
                "Settings are declared, and ISIK_FEATURES_OVERRIDES_MODEL names no model to keep them in - "
                "declare one on AbstractOverrides."
            )
        )
    if overrides is not None and not issubclass(overrides, AbstractOverrides):
        problems.append(_("%(model)s isn't an AbstractOverrides.") % {"model": overrides._meta.label})
        # With no holder key of its own, nothing below can be asked of it.
        overrides = None
    if overrides is not None and "pgh_event_model" not in dir(overrides):
        problems.append(
            _(
                "%(model)s keeps forced settings and has no history - who forced one, when and why is gone once it's "
                "released. Track it with @track_events()."
            )
            % {"model": overrides._meta.label}
        )
    grants = get_grant_model()
    if grants._meta.get_field("holder").related_model is not get_holder_model():
        problems.append(
            _(
                "%(model)s's grants are of %(granted)s, and ISIK_FEATURES_HOLDER_MODEL is %(holder)s - declare a grant "
                "model of your own on AbstractFeatureGrant and name it in ISIK_FEATURES_GRANT_MODEL."
            )
            % {
                "model": grants._meta.label,
                "granted": grants._meta.get_field("holder").related_model._meta.label,
                "holder": get_holder_model()._meta.label,
            }
        )
    problems += grant_history_problems(grants)
    for model in (grants, overrides):
        if model is not None and model._meta.get_field("holder").remote_field.on_delete is models.CASCADE:
            problems.append(
                _(
                    "%(model)s's holder key cascades - its rows refuse a delete, and their history would outlive "
                    "them; on_delete=ERASE erases both."
                )
                % {"model": model._meta.label}
            )
    return problems


def _every_feature_problems(declared):
    """What keeps `HOLDS_EVERY_FEATURE` from naming holders: anything but None, a `Q`, or a boolean field's name."""
    if declared is None or isinstance(declared, models.Q):
        return []
    holder = get_holder_model()
    field = next((field for field in holder._meta.concrete_fields if field.name == declared), None)
    if field is not None and isinstance(getattr(field, "output_field", field), models.BooleanField):
        return []
    return [
        _(
            "ISIK['DJANGO']['FEATURES']['HOLDS_EVERY_FEATURE'] is %(declared)r - it's a Q over %(model)s, or the name "
            "of a boolean field of it."
        )
        % {"declared": declared, "model": holder._meta.label}
    ]


def grant_history_problems(grants):
    """
    What keeps `grants`' history from saying when each grant was revoked - the only record of it: none, one that
    can be edited, or one that doesn't record a change of status.
    """
    label = grants._meta.label
    event = getattr(grants, "pgh_event_model", None)
    if event is None:
        return [
            _(
                "%(model)s has no history - it's what says when each grant was revoked, and the sweep reads it. "
                "Track it with @track_events()."
            )
            % {"model": label}
        ]
    problems = []
    if "status" not in {field.name for field in event._meta.fields}:
        problems.append(_("%(model)s's history leaves out status - track it.") % {"model": label})
    triggers = {trigger.name for trigger in getattr(event._meta, "triggers", [])}
    if not set(APPEND_ONLY_TRIGGERS) <= triggers:
        problems.append(
            _(
                "%(model)s's history can be edited - when a grant was revoked has to stay as written. Keep it "
                "append-only."
            )
            % {"model": label}
        )
    if not any(_records_status(trigger) for model, trigger in pgtrigger.registered() if model is grants):
        problems.append(
            _("%(model)s's history records no update changing status - a revoke would leave no time.")
            % {"model": label}
        )
    return problems


def _records_status(trigger):
    """Whether `trigger` is a history event on update that a change of status alone fires."""
    if not isinstance(trigger, pghistory.trigger.Event) or "UPDATE" not in str(trigger.operation):
        return False
    condition = trigger.condition
    if condition is None:
        return True
    return (
        isinstance(condition, pgtrigger.AnyChange)
        and not condition._negated
        and (not condition.fields or "status" in condition.fields)
        and "status" not in condition.exclude
    )


def _null_problems(setting):
    """What's wrong with whether `setting` may be None: only one with a null_label is, and that one's default is."""
    if setting.null_label is None:
        if setting.field.null or setting.default is None:
            return [
                _(
                    "%(setting)s could be null - a setting has a value unless a null_label says what None is, so its "
                    "field takes no null=True and its default isn't None. Give it a null_label for a None that "
                    "means something."
                )
                % {"setting": setting.name}
            ]
        return []
    problems = []
    if not setting.field.null:
        problems.append(
            _("%(setting)s has a null_label, and its field takes no null=True - give it null=True, or drop the label.")
            % {"setting": setting.name}
        )
    if setting.default is not None:
        problems.append(
            _(
                "%(setting)s has a null_label, so its default is None - a holder's own None and no choice at all "
                "would read alike otherwise."
            )
            % {"setting": setting.name}
        )
    if any(value is None for value, _label in setting.field.flatchoices):
        problems.append(
            _(
                "%(setting)s's field lists None among its choices, and it has a null_label - say what None is in "
                "one of them."
            )
            % {"setting": setting.name}
        )
    return problems


def declaration_problems():
    """The problems with the declared features and settings."""
    problems = []
    holder_fields = {field.name for field in get_holder_model()._meta.get_fields()}
    for setting in declarations.settings.values():
        problems += _null_problems(setting)
        if isinstance(setting.own, declarations.OnHolder) and setting.own.field not in holder_fields:
            problems.append(
                _("%(setting)s's own value is the holder's %(field)s, which %(model)s has no field called.")
                % {"setting": setting.name, "field": setting.own.field, "model": get_holder_model()._meta.label}
            )
    for name in declarations.features:
        if not declarations.settings_unlocked_by(name) and name not in declarations.checked:
            problems.append(
                _(
                    "%(feature)s unlocks no setting and nothing checks it - give a setting feature=, or check it "
                    "with HasFeature or requires_feature()."
                )
                % {"feature": name}
            )
    return problems


def text_problems():
    """Each feature or setting a client listing them could only show by its name."""
    return [
        _("The feature %(name)s has no label - give it label=.") % {"name": name}
        for name, feature in declarations.features.items()
        if feature.label is None
    ] + [
        _("The setting %(name)s has no label - give it label=.") % {"name": name}
        for name, setting in declarations.settings.items()
        if setting.label is None
    ]


@isik_check(Check.FEATURES)
def features_can_work(options, **kwargs):
    """A features setting or declaration that can't work - see `setting_problems()` and `declaration_problems()`."""
    hint = _("See isik.django.apps.features.")
    return [Error(problem, hint=hint, id="isik.E017") for problem in setting_problems() + declaration_problems()]
