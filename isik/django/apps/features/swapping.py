"""
The features app's swappable settings and their getters. `ISIK_FEATURES_HOLDER_MODEL` names what features are
granted to - `AUTH_USER_MODEL` unless set (an organization, say). The catalog, grant and sweep models are isik's
unless a project owns them; the overrides model is always the project's own (`AbstractOverrides`), named by
`ISIK_FEATURES_OVERRIDES_MODEL` - there's none until it declares one.
"""

from django.apps import apps
from django.conf import settings


HOLDER_MODEL = "ISIK_FEATURES_HOLDER_MODEL"
FEATURE_MODEL = "ISIK_FEATURES_FEATURE_MODEL"
GRANT_MODEL = "ISIK_FEATURES_GRANT_MODEL"
SWEEP_MODEL = "ISIK_FEATURES_SWEEP_MODEL"
OVERRIDES_MODEL = "ISIK_FEATURES_OVERRIDES_MODEL"

DEFAULTS = {
    FEATURE_MODEL: "isik_features.Feature",
    GRANT_MODEL: "isik_features.FeatureGrant",
    SWEEP_MODEL: "isik_features.SweepCursor",
}


def set_defaults():
    """Gives each swappable setting the project leaves unset its default - the holder the user model's."""
    if not hasattr(settings, HOLDER_MODEL):
        setattr(settings, HOLDER_MODEL, settings.AUTH_USER_MODEL)
    for name, default in DEFAULTS.items():
        if not hasattr(settings, name):
            setattr(settings, name, default)


def model_name(setting):
    """The `"app_label.Model"` the swappable `setting` names - for a key to it, declared at class definition."""
    return getattr(settings, setting)


def get_holder_model():
    """What features are granted to: `ISIK_FEATURES_HOLDER_MODEL`'s model."""
    return apps.get_model(getattr(settings, HOLDER_MODEL))


def get_feature_model():
    """The feature catalog model: `ISIK_FEATURES_FEATURE_MODEL`'s, else isik's."""
    return apps.get_model(getattr(settings, FEATURE_MODEL))


def get_grant_model():
    """The feature grant model: `ISIK_FEATURES_GRANT_MODEL`'s, else isik's."""
    return apps.get_model(getattr(settings, GRANT_MODEL))


def get_sweep_model():
    """The sweep cursor model: `ISIK_FEATURES_SWEEP_MODEL`'s, else isik's."""
    return apps.get_model(getattr(settings, SWEEP_MODEL))


def get_overrides_model():
    """The project's overrides model, `ISIK_FEATURES_OVERRIDES_MODEL` - None when it declares none."""
    name = getattr(settings, OVERRIDES_MODEL, None)
    return apps.get_model(name) if name else None
