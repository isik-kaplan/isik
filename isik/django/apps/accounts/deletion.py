"""
A person deleting their own account:

    deletes_at = request_deletion(request.user)     # locked: every session ended; deleted at deletes_at
    cancel_pending_deletion(user)                    # from the login - True when signing in cancelled one
    sweep_deletions()                                # from the beat - deletes every account whose time has come

What the API asking for it looks like is the project's - behind its own re-authentication.
"""

import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from isik.django.apps.accounts import conf
from isik.django.apps.accounts.signals import account_deleted, account_deletion_cancelled, account_deletion_requested


def request_deletion(user):
    """
    Has `user`'s account deleted after `DELETION_AFTER`, and ends every session of theirs now. Asking again keeps
    the first date. Returns when it's deleted.
    """
    with transaction.atomic():
        if user.deletes_at is None:
            user.deletes_at = timezone.now() + conf.setting("DELETION_AFTER")
        user.session_epoch = uuid.uuid4()
        user.save(update_fields=["deletes_at", "session_epoch"])
        account_deletion_requested.send(sender=type(user), user=user, deletes_at=user.deletes_at)
    return user.deletes_at


def cancel_pending_deletion(user):
    """Cancels `user`'s pending deletion - for the login to call once they've signed in. True when one was."""
    if user.deletes_at is None:
        return False
    user.deletes_at = None
    user.save(update_fields=["deletes_at"])
    account_deletion_cancelled.send(sender=type(user), user=user)
    return True


def delete_account(user):
    """Deletes `user`'s account now, with the `DELETION_BACKEND`, in one transaction."""
    user_id = user.pk
    with transaction.atomic():
        conf.deletion_backend().delete(user)
        transaction.on_commit(lambda: account_deleted.send(sender=get_user_model(), user_id=user_id))


def sweep_deletions(now=None):
    """Deletes every account whose pending deletion's time has come. Returns how many."""
    now = timezone.now() if now is None else now
    due = list(get_user_model()._default_manager.filter(deletes_at__lte=now))
    for user in due:
        delete_account(user)
    return len(due)
