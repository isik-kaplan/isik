"""
Declaring features and the settings they unlock - in code, like notification kinds:

    WEBHOOKS = declare_feature("webhooks", label=_("Webhooks"), description=_("Send events to your own URLs."))
    LONG_LIVED_TOKENS = declare_feature("long_lived_tokens", label=_("Long-lived tokens"))

    declare_setting(
        "max_endpoints", models.PositiveIntegerField(), default=0, feature=WEBHOOKS, label=_("Webhook endpoints")
    )
    declare_setting(
        "token_lifetime_days",
        models.PositiveIntegerField(),
        default=30,
        feature=LONG_LIVED_TOKENS,
        without_feature=AtMost(90),
        label=_("Token lifetime"),
    )

A setting's value resolves as a force first, then what a missing feature does, then the holder's own value -
see `resolution.effective()`. What a missing feature does is the setting's own: `Default()` replaces the
holder's value with the default; `AtMost(n)` / `AtLeast(n)` clamp it to a bound. The holder's own value lives on
the project's overrides model (`OnOverrides()`, the default) or on the holder itself (`OnHolder("field")`).
"""

from dataclasses import dataclass, field

from django.core.exceptions import ImproperlyConfigured
from django.db import models

from isik._internal.translation import gettext as _


class FeatureName(str):
    """A declared feature's name - what `declare_feature()` returns - carrying what it says to people."""

    def __new__(cls, name, *, label=None, description=None, visible_to_holder=True):
        declared = super().__new__(cls, name)
        declared.label, declared.description, declared.visible_to_holder = label, description, visible_to_holder
        return declared


# Every declared feature, by name, in the order declared.
features = {}
# The features `HasFeature`/`requires_feature()` check in code - a use beside unlocking a setting.
checked = set()


def declare_feature(name, *, label=None, description=None, visible_to_holder=True):
    """Declares the feature `name` and returns its `FeatureName`. A name declared twice is refused."""
    if name in features:
        raise ImproperlyConfigured(_("A feature called %(name)s is declared already.") % {"name": name})
    declared = FeatureName(name, label=label, description=description, visible_to_holder=visible_to_holder)
    features[name] = declared
    return declared


@dataclass(frozen=True)
class Default:
    """Without the feature, the setting is its default, whatever the holder chose."""

    def locked(self, default):
        return default


@dataclass(frozen=True)
class Fixed:
    """Without the feature, the setting is `value`, whatever the holder chose - 0 endpoints, say, where it gives 5."""

    value: object

    def locked(self, default):
        return self.value


# What a missing feature locks - the holder can't change the setting until they have it.
LOCKING = (Default, Fixed)


@dataclass(frozen=True)
class AtMost:
    """Without the feature, the holder's own value, no higher than `bound`."""

    bound: object

    def apply(self, value):
        return min(value, self.bound)


@dataclass(frozen=True)
class AtLeast:
    """Without the feature, the holder's own value, no lower than `bound`."""

    bound: object

    def apply(self, value):
        return max(value, self.bound)


@dataclass(frozen=True)
class OnOverrides:
    """The holder's own value is a column of the project's overrides row - null until the holder chooses."""


@dataclass(frozen=True)
class OnHolder:
    """
    The holder's own value is the holder's field `field` - an existing column of its, part of its API. With
    `resolved_as`, the holder has the setting's effective value at that attribute too - `organization.mfa_policy`
    reading what's in force, forced or not - and setting it sets `field`, the holder's own.

    `resolved_as` is never `field` itself: Django's `save()` and `refresh_from_db()` read a field through its
    attribute, and would keep a forced value as the holder's own - which a release then couldn't undo. Keep the own
    value under another name (`db_column` keeps its column) and resolve at the name call sites read.
    """

    field: str
    resolved_as: str = None


# A setting's type as a client reads it - the discriminator of its value's shape in the schema.
TYPES = (
    (models.BooleanField, "bool"),
    (models.DurationField, "duration"),
    (models.IntegerField, "int"),
    (models.DecimalField, "decimal"),
    (models.FloatField, "float"),
    (models.CharField, "text"),
    (models.TextField, "text"),
)


@dataclass(frozen=True)
class Setting:
    """One declared setting. Built by `declare_setting()`, not by hand."""

    name: str
    field: models.Field
    default: object
    feature: str = None
    without_feature: object = field(default_factory=Default)
    own: object = field(default_factory=OnOverrides)
    label: object = None
    description: object = None
    visible_to_holder: bool = True
    holder_may_change: bool = True
    request_policies: tuple = ()

    @property
    def type(self):
        """`choice` for a field with choices, else what its class is - `int`, `bool`, `duration`, ..."""
        if self.field.choices:
            return "choice"
        return next((name for kind, name in TYPES if isinstance(self.field, kind)), "text")


# Every declared setting, by name, in the order declared.
settings = {}


def declare_setting(
    name,
    field,
    default,
    *,
    feature=None,
    without_feature=None,
    own=None,
    label=None,
    description=None,
    visible_to_holder=True,
    holder_may_change=True,
    request_policies=(),
):
    """
    Declares the setting `name`: a value of `field`'s type, `default` until the holder chooses. With `feature`,
    `without_feature` says what it is while the holder lacks it (`Default()`, `Fixed(value)`, or
    `AtMost(n)`/`AtLeast(n)`); `own` where the holder's own value lives (`OnOverrides()`, or `OnHolder("field")`);
    `holder_may_change=False` for one the holder reads and only staff set; `request_policies` the project's request
    policies its change has to pass (a fresh re-authentication, say). Returns the `Setting`.
    """
    if name in settings:
        raise ImproperlyConfigured(_("A setting called %(name)s is declared already.") % {"name": name})
    if isinstance(own, OnHolder) and own.resolved_as == own.field:
        raise ImproperlyConfigured(
            _(
                "%(setting)s resolves as %(field)s, its own value's field - a save would keep a forced value as the "
                "holder's own. Keep the own value in a field of another name."
            )
            % {"setting": name, "field": own.field}
        )
    if feature is not None and str(feature) not in features:
        raise ImproperlyConfigured(
            _("%(setting)s needs the feature %(feature)s, which isn't declared.")
            % {"setting": name, "feature": feature}
        )
    declared = Setting(
        name=name,
        field=field,
        default=default,
        feature=str(feature) if feature is not None else None,
        without_feature=without_feature or Default(),
        own=own or OnOverrides(),
        label=label,
        description=description,
        visible_to_holder=visible_to_holder,
        holder_may_change=holder_may_change,
        request_policies=tuple(request_policies),
    )
    settings[name] = declared
    return declared


def settings_unlocked_by(feature):
    """The settings `feature` unlocks, by name."""
    return [name for name, declared in settings.items() if declared.feature == str(feature)]
