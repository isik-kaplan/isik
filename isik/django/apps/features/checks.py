"""
The features app's system checks - `isik.E017` (`Check.FEATURES`) for a setting or declaration that can't work,
and its share of `isik.E016` (`Check.LABELS`): a feature or setting that can't say what it is.
"""

from django.core.checks import Error
from django.db import models

from isik._internal.translation import gettext as _
from isik.django.apps.common.checks import Check, isik_check
from isik.django.apps.features import conf, declarations
from isik.django.apps.features.bases import AbstractOverrides
from isik.django.apps.features.swapping import get_grant_model, get_holder_model, get_overrides_model


def setting_problems():
    """The problems with `ISIK_FEATURES` and the overrides model."""
    problems = []
    unknown = sorted(set(conf.configured()) - set(conf.DEFAULTS))
    if unknown:
        problems.append(_("ISIK_FEATURES has keys it doesn't know: %(keys)s.") % {"keys": ", ".join(unknown)})
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


def declaration_problems():
    """The problems with the declared features and settings."""
    problems = []
    holder_fields = {field.name for field in get_holder_model()._meta.get_fields()}
    for setting in declarations.settings.values():
        if setting.field.null or setting.default is None:
            problems.append(
                _(
                    "%(setting)s could be null - a setting always has a value, so its field takes no null=True and "
                    "its default isn't None."
                )
                % {"setting": setting.name}
            )
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
