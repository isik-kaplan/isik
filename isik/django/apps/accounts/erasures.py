"""
What a person's rows are, model by model - registered once, any model's, a library's included:

    from isik.django.apps.accounts.erasures import erasure

    erasure(User, anonymize={"email": lambda user: f"deleted-{user.pk}@invalid", "first_name": "", "phone": ""})
    erasure(EmailAddress, delete=True)                       # allauth's: the tombstone needs none of them
    erasure(Invoice, via="account__owner", anonymize={"billing_name": ""})

A registered model's rows of the person - its key to the user model, or the lookup `via` names - are deleted with
their history when the account is erased. When it's anonymized, `delete=True` rows are deleted with their history,
and the others keep their rows with `anonymize`'s fields set - a value, or f(row) - in their history too.

isik registers its own tables itself. `isik.E029` holds every key to the user model to CASCADE, SET_NULL, or a
model registered here.
"""

from dataclasses import dataclass, field
from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured

from isik._internal.translation import gettext as _
from isik.django.apps.accounts import conf


@dataclass(frozen=True)
class Erasure:
    """One registered model. Built by `erasure()`, not by hand."""

    model: str
    via: str = None
    delete: bool = False
    anonymize: dict = field(default_factory=dict)

    def resolved(self):
        """The model registered, loaded."""
        return apps.get_model(self.model)

    def lookup(self):
        """The lookup from a row of the model to the person: `via`, else its one key to the user model."""
        if self.via is not None:
            return self.via
        model = self.resolved()
        if model is get_user_model():
            return "pk"
        keys = [
            each.name
            for each in model._meta.concrete_fields
            if each.is_relation and each.related_model is get_user_model()
        ]
        if len(keys) != 1:
            raise ImproperlyConfigured(
                _("%(model)s has %(count)s keys to the user model - name the person's with via=.")
                % {"model": self.model, "count": len(keys)}
            )
        return keys[0]

    def rows_of(self, user):
        """The person's rows of the model."""
        return self.resolved()._default_manager.filter(**{self.lookup(): user.pk})


# Every registered model, by "app_label.Model".
erasures = {}


def erasure(model, *, via=None, delete=False, anonymize=None):
    """
    Registers `model` - a class or "app_label.Model" - as holding a person's rows: found by `via` (a lookup to the
    user, else its key to the user model), deleted in either mode with `delete=True`, else, when the account is
    anonymized, kept with `anonymize`'s fields set. Returns the `Erasure`.
    """
    label = model if isinstance(model, str) else model._meta.label
    if delete and anonymize:
        raise ImproperlyConfigured(
            _("%(model)s's rows are deleted or anonymized, not both - give delete=True or anonymize.")
            % {"model": label}
        )
    registered = Erasure(model=label, via=via, delete=delete, anonymize=dict(anonymize or {}))
    erasures[label] = registered
    return registered


def register_isiks_own():
    """Registers isik's own apps' rows of a person, for each app installed - the project names none of them."""
    if apps.is_installed("isik.django.apps.notifications.audience"):
        from isik.django.apps.notifications.audience.swapping import (
            get_device_model,
            get_inbox_item_model,
            get_subscription_model,
        )

        for model in (get_subscription_model(), get_inbox_item_model(), get_device_model()):
            erasure(model, delete=True)
    claims = {
        "isik.django.apps.idempotency.by_reference": "idempotency_by_reference.IdempotencyClaim",
        "isik.django.apps.idempotency.with_body": "idempotency_with_body.IdempotencyClaimWithBody",
    }
    for app, label in claims.items():
        if apps.is_installed(app):
            erasure(label, via="claimed_by", delete=True)


def import_erasures_module():
    """Imports `ERASURES`, the module registering the project's models - where it's named."""
    path = conf.setting("ERASURES")
    if path:
        import_module(path)
