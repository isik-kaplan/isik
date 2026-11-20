"""
`isik.django.apps.accounts` - a person deleting their own account: locked and signed out at once, deleted after
`DELETION_AFTER` by a sweep unless they sign back in first, and then erased or anonymized by the backend the
project picks - isik's own tables' rows of theirs with it, and every model the project registers.

Add `DeletableAccount` to the user model, register what a person's rows are (`erasures.erasure()`), configure with
`ISIK['DJANGO']['ACCOUNTS']` (see `conf`), call `deletion.cancel_pending_deletion()` from your login, and run
`tasks.sweep_deletions` from your beat schedule.
"""
