"""
Checking a feature in code, beside the settings it unlocks - for a capability that isn't a setting (every user may
register a webhook, but only with the feature). Both are made where the module is loaded, so the check that every
feature is used counts them before anything runs:

    needs_webhooks = requires_feature(WEBHOOKS)   # a guard: needs_webhooks(user) raises FeatureMissing without it
    permission_classes = [HasFeature(WEBHOOKS)]    # isik.django.apps.features.drf
"""

from isik._internal.translation import gettext as _
from isik.django.apps.features import declarations
from isik.django.apps.features.resolution import has_feature


class FeatureMissing(PermissionError):
    """The holder lacks a feature an act needs. `code` names the refusal, for a client."""

    code = "feature_missing"


def checks_feature(feature):
    """Marks `feature` as checked in code - a use the check that every feature is used counts. Returns its name."""
    name = str(feature)
    declarations.checked.add(name)
    return name


def requires_feature(feature):
    """A guard: called with a holder, it raises `FeatureMissing` unless `feature` is in force for them."""
    name = checks_feature(feature)

    def guard(holder):
        if not has_feature(holder, name):
            raise FeatureMissing(_("This needs the feature %(feature)s.") % {"feature": name})

    return guard
