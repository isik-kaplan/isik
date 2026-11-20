# accounts

A person deleting their own account. Asking locks it at once - every session of theirs ends - and a sweep deletes it
after `DELETION_AFTER`, unless they sign back in first, which cancels it. Deleting it erases or anonymizes it, by the
backend the project picks: the person's own row, isik's tables' rows of theirs, and every model the project
registers - their history with them.

What the API asking for it looks like - and the re-authentication in front of it - is the project's.

## Setup

```python
INSTALLED_APPS = [..., "isik.django.apps.accounts"]

class User(DeletableAccount, AbstractUser):      # isik.django.apps.accounts.bases
    ...

ISIK = {"DJANGO": {"ACCOUNTS": {
    "DELETION_AFTER": timedelta(days=14),        # how long a person has to change their mind
    "DELETION_BACKEND": "isik.django.apps.accounts.backends.Anonymize",   # or Erase, the default
    "ERASURES": "users.erasures",                # the module registering the project's models
}}}

CELERY_BEAT_SCHEDULE = {**tasks.beat_schedule(every=3600)}    # isik.django.apps.accounts.tasks
```

`DeletableAccount` adds two columns to the user model, `deletes_at` and `session_epoch`. `DELETION_AFTER` has no
default - how long a person has is the project's to say - and `isik.E029` asks for it; `timedelta(0)` deletes at
the next sweep.

## Asking, cancelling, deleting

```python
from isik.django.apps.accounts.deletion import cancel_pending_deletion, request_deletion

deletes_at = request_deletion(request.user)   # every session ended; deleted at deletes_at - asked again, the same
cancel_pending_deletion(user)                 # True when signing in cancelled a pending deletion
user.is_pending_deletion
```

- **Every session ends** when a person asks: `DeletableAccount` signs sessions with `session_epoch` beside the
  password, and asking moves it. The password stays, so the person can still sign in - and signing in is how they
  change their mind. Until the first move, a session is signed as Django signs it, so adding the mixin signs nobody
  out. API tokens are the project's: revoke them from `account_deletion_requested`.
- **Cancelling** is the login's to call - every way a person signs in (a password, a social login, an emailed code)
  calls `cancel_pending_deletion(user)` once they have, and tells them when it answers True. The login has to admit
  a pending account to do so: `is_pending_deletion` is what it reads.
- **Deleting** - `tasks.sweep_deletions` (or `deletion.sweep_deletions()`) deletes every account whose `deletes_at`
  has come; `deletion.delete_account(user)` deletes one now. Each in a transaction of its own.

Signals (`isik.django.apps.accounts.signals`): `account_deletion_requested(user, deletes_at)`,
`account_deletion_cancelled(user)`, and `account_deleted(user_id)` once the deleting transaction commits.

## Erase or anonymize

| backend | the person's row | a registered model's rows of theirs |
|---|---|---|
| `Erase` | deleted, with everything its delete cascades to | deleted |
| `Anonymize` | kept as a tombstone: its registered fields set, the password unusable, inactive | `delete=True`: deleted; else kept, with `anonymize`'s fields set |

Either way, a row deleted takes its history (every `@track_events()` event of it) with it, and a field anonymized is
anonymized in its history's copies too - a history row still holding the old email would undo it. Who did what, and
when, stays: an anonymized person's rows still name the tombstone, and an erased person's id in a history row's
context resolves to nothing.

A backend of the project's own is a class with `delete(user)`, called inside the deleting transaction.

## What a person's rows are: `erasure()`

```python
from isik.django.apps.accounts.erasures import erasure

erasure(User, anonymize={"email": lambda user: f"deleted-{user.pk}@invalid", "first_name": "", "phone": ""})
erasure(EmailAddress, delete=True)           # allauth's - the tombstone needs none of them
erasure(SocialAccount, delete=True)
erasure(Invoice, via="account__owner", anonymize={"billing_name": ""})
```

- A model is a class or `"app_label.Model"` - any model, a library's included.
- Its rows of the person are found by its one key to the user model, or by the lookup `via` names.
- `delete=True` deletes them in either mode; `anonymize` - each field a value, or f(row) - sets them when the account
  is anonymized. Erased, every registered model's rows of the person are deleted.
- The user model itself is registered with what anonymizing sets on it - `isik.E029` asks for it with `Anonymize`.

isik registers its own tables itself, for each app installed: subscriptions, inbox items and devices, and
idempotency claims, deleted in either mode. Features' grants and overrides go with the holder (`ERASE`).

## What the check holds you to

`isik.E029` refuses a user model that isn't a `DeletableAccount`, a `DELETION_AFTER` that isn't a timedelta, a
backend that can't be made or has no `delete(user)`, a registration that can't find its rows - and **every key to the
user model** that isn't `CASCADE`, `SET_NULL`, an `on_delete` removing the rows itself (features' `ERASE`), or on a
registered model. A `PROTECT` would block a person's deletion; a `DO_NOTHING` would leave their rows behind, keyed to
nothing. History tables are left out: their keys are copies of the rows'.
