"""
`isik.E029` - deleting an account can't work, or would leave a person's rows behind: see `account_problems()`.
"""

from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.checks import Error, Tags
from django.core.exceptions import ImproperlyConfigured
from django.db import models

from isik._internal.translation import gettext as _
from isik.django.apps.accounts import conf
from isik.django.apps.accounts.backends import Anonymize
from isik.django.apps.accounts.bases import DeletableAccount
from isik.django.apps.accounts.erasures import erasures
from isik.django.apps.common.checks import Check, isik_check


AREA = "ISIK['DJANGO']['ACCOUNTS']"
# What a key to the user model may do on its own when the person's row goes: their row goes with it, or forgets them.
SETTLED = (models.CASCADE, models.SET_NULL)


def setting_problems():
    """What's wrong with `ISIK['DJANGO']['ACCOUNTS']`, one sentence each."""
    problems = []
    unknown = sorted(set(conf.configured()) - set(conf.DEFAULTS))
    if unknown:
        problems.append(_("%(area)s has keys it doesn't know: %(keys)s.") % {"area": AREA, "keys": ", ".join(unknown)})
    after = conf.setting("DELETION_AFTER")
    if not isinstance(after, timedelta) or after < timedelta(0):
        problems.append(
            _("%(area)s['DELETION_AFTER'] is how long a person has to change their mind, a timedelta.") % {"area": AREA}
        )
    try:
        backend = conf.deletion_backend()
    except (ImportError, TypeError) as missing:
        return [
            *problems,
            _("%(area)s['DELETION_BACKEND'] can't be made: %(error)s") % {"area": AREA, "error": missing},
        ]
    if not callable(getattr(backend, "delete", None)):
        problems.append(_("%(area)s['DELETION_BACKEND'] has no delete(user).") % {"area": AREA})
    if isinstance(backend, Anonymize) and get_user_model()._meta.label not in erasures:
        problems.append(
            _(
                "%(area)s anonymizes an account, and nothing says what of %(model)s's to set - register it with "
                "erasure(%(model)s, anonymize={...})."
            )
            % {"area": AREA, "model": get_user_model()._meta.label}
        )
    return problems


def _history(model):
    # A history table's keys are its copies of the row's - an id that resolves to nothing once the person is gone.
    return hasattr(model, "pgh_tracked_model")


def account_problems():
    """What would make deleting an account fail, or leave a person's rows behind, one sentence each."""
    user = get_user_model()
    problems = setting_problems()
    if not issubclass(user, DeletableAccount):
        problems.append(
            _("%(model)s isn't a DeletableAccount - add it to the user model's bases.") % {"model": user._meta.label}
        )
    for registered in erasures.values():
        try:
            registered.lookup()
        except (LookupError, ImproperlyConfigured) as unusable:
            problems.append(str(unusable))
    for model in apps.get_models():
        if _history(model) or model._meta.proxy or not model._meta.managed or model._meta.label in erasures:
            continue
        for key in model._meta.concrete_fields:
            if not key.is_relation or key.related_model is not user or key.remote_field.on_delete in SETTLED:
                continue
            # An on_delete that removes the rows itself - features' ERASE - says so.
            if "settles_deletion" in vars(key.remote_field.on_delete):
                continue
            problems.append(
                _(
                    "%(model)s.%(key)s keys to the user model with %(on_delete)s - a person's rows there would block "
                    "their deletion or outlive it. CASCADE, SET_NULL, or register %(model)s with erasure()."
                )
                % {"model": model._meta.label, "key": key.name, "on_delete": key.remote_field.on_delete.__name__}
            )
    return problems


@isik_check(Check.ACCOUNTS, Tags.models)
def accounts_can_work(options, **kwargs):
    """Deleting an account can't work, or would leave a person's rows behind - see `account_problems()`."""
    hint = _("See isik.django.apps.accounts.")
    return [Error(problem, hint=hint, id="isik.E029") for problem in account_problems()]
