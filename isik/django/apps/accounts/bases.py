"""
`DeletableAccount` - the user model's half of deleting an account. A project adds it to its own user model:

    class User(DeletableAccount, AbstractUser):
        ...

It carries when a pending deletion happens (`deletes_at`) and a mark every session of the person is signed with
(`session_epoch`): moving it ends them all at once, without touching the password, so the person can still sign
back in to cancel.
"""

from django.db import models
from django.utils.crypto import salted_hmac

from isik._internal.translation import gettext_lazy


class DeletableAccount(models.Model):
    deletes_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When the account is deleted, while its deletion is pending."),
        db_comment="When a pending deletion of the account happens - signing in before then cancels it. Null "
        "with none pending.",
        verbose_name=gettext_lazy("deletes at"),
    )
    session_epoch = models.UUIDField(
        null=True,
        blank=True,
        editable=False,
        help_text=gettext_lazy("What every session of the person is signed with - moved to end them all."),
        db_comment="Mixed into the session hash: a new one ends every session of the person. Null until the "
        "first move, which leaves the hash Django's own.",
        verbose_name=gettext_lazy("session epoch"),
    )

    class Meta:
        abstract = True

    @property
    def is_pending_deletion(self):
        """Whether the person asked for their account to be deleted, and it hasn't been yet."""
        return self.deletes_at is not None

    def get_session_auth_hash(self):
        # Django's own while no session has been ended this way, so adding the mixin signs nobody out.
        if self.session_epoch is None:
            return super().get_session_auth_hash()
        key_salt = "isik.django.apps.accounts.bases.DeletableAccount.get_session_auth_hash"
        return salted_hmac(key_salt, f"{self.password}:{self.session_epoch}", algorithm="sha256").hexdigest()
