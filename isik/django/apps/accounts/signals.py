"""
Signals of an account's deletion - for the project to do what only it knows: revoke the person's API tokens when
they ask, tell them, keep its own records.

- `account_deletion_requested(sender=user model, user, deletes_at, first)` - they asked; their sessions are ended.
  Sent on every request, `first` True for the one that set `deletes_at` - a repeat keeps the date, and still ends
  the sessions. A receiver telling them "your account will be deleted" tells them when `first` is.
- `account_deletion_cancelled(sender=user model, user)` - they signed back in before `deletes_at`.
- `account_deleted(sender=user model, user_id)` - erased or anonymized, once the deleting transaction commits.
"""

from django.dispatch import Signal


account_deletion_requested = Signal()
account_deletion_cancelled = Signal()
account_deleted = Signal()
