"""
Signals of an account's deletion - for the project to do what only it knows: revoke the person's API tokens when
they ask, tell them, keep its own records.

- `account_deletion_requested(sender=user model, user, deletes_at)` - they asked; their sessions are ended.
- `account_deletion_cancelled(sender=user model, user)` - they signed back in before `deletes_at`.
- `account_deleted(sender=user model, user_id)` - erased or anonymized, once the deleting transaction commits.
"""

from django.dispatch import Signal


account_deletion_requested = Signal()
account_deletion_cancelled = Signal()
account_deleted = Signal()
