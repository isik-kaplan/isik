# Audiences, policies and subscriptions

## Audiences

An audience is anything with `recipients(notice)`, returning the users the notice is for - read inside
the partition it's being delivered in. Two ship:

- **`Field(*fields, at=SNAPSHOT)`** - the users the subject row names by its keys to the user model:
  `Field("from_user", "to_user")`. Read from the notice's snapshot - who they were when it happened -
  so the kind's `snapshot` must carry each field (`isik.E014` says so when it doesn't). `at=LIVE` reads
  the row as it is when the notice is delivered instead, for a kind meaning "whoever holds it now". It
  is never a fallback: a notice resolves the same recipients however many times it's retried.
- **`Subscribers()`** - everyone with an enabled subscription covering the notice's kind and variant.

Your own is a small class:

```python
# audiences.py
from django.contrib.auth import get_user_model

from isik.django.apps.notifications.audiences import Audience


class Operators(Audience):
    def recipients(self, notice):
        return get_user_model().objects.filter(is_staff=True)


class InstallationHolders(Audience):
    spans_partitions = True

    def partitions(self, notice):
        from apps.installations.models import Installation  # inside: see below

        return Installation.objects.filter(app_id=notice.subject_id).values_list("schema_name", flat=True)

    def recipients(self, notice):
        from apps.installations.models import Installation

        return Installation.owners_here()
```

**Import your models inside the methods.** A kind is declared on a model, and its `audience=` is imported
there, at class definition - so an audience module importing models at its top is a circular import the
first time one is used: `cannot import name ... from partially initialized module`. `get_user_model()` is
safe at the top; your own models aren't.

`partitions(notice)` names the partitions the notice reaches (its subject's own by default), and
`spans_partitions` says whether that can be more than one - a single-partition kind's audience mustn't.
Return `snapshot_fields()` from your own audience if it reads the snapshot, so the check holds the
kind's snapshot to it.

## Policies

Each kind has one `Policy`:

| policy | who gets it |
|---|---|
| `MANDATORY` | everyone the audience names - subscriptions are ignored |
| `DEFAULT_ON` | everyone the audience names, but those who turned it off |
| `DEFAULT_OFF` | only those the audience names who turned it on |

Webhook endpoints are separate: an endpoint gets every notice of a kind (and variant) its
`EndpointKind` rows ask for, whoever the audience is.

## Subscriptions

A subscription is a deviation from the policy - so a newly declared kind reaches everyone it should
with nothing to backfill. One row per user, kind and variant: no variant covers every one.

```python
from isik.django.apps.notifications.audience.helpers import subscribe, unsubscribe

subscribe(user, "parcel_flagged", "lost")             # on, for one variant
subscribe(user, "parcel_flagged", enabled=False)      # off, for every variant - replacing the named ones
unsubscribe(user, "parcel_flagged")                   # back to the policy
```

A row naming no variant can't sit beside one naming a variant for the same user and kind - the first
already covers the second. The database refuses it (`VariantGuard`, under an advisory lock so two
writers can't both pass), and refuses a kind that doesn't exist and a variant the kind doesn't declare -
so a `kind` key without a database constraint, one reaching across schemas, is still checked.
The guard is the trigger `guard_variant` on each kind-holder, and `protect_policy_delete` on
`NoticeKindPolicy` keeps a kind from ever being deleted - so a `kind` key with no constraint of its own
can't dangle. Both names are stable: a project may name them as what guards its keys.
`subscribe()` with no variant removes the named rows first, in one transaction; naming one beside a row
covering every variant is refused - `unsubscribe()` the kind first. Endpoint kinds and inbox items are held to declared variants
the same way.

A `MANDATORY` kind has no choice to record: `subscribe()` refuses it (`KindIsMandatory`, code
`kind_mandatory`), so no row says a person turned off what they can't. Each refusal is a
`SubscriptionRefused` carrying its `code`.

## Forcing one person's subscription

Staff turning a kind back on (or off) for one person, which they then can't change, is an optional model
base - a project that never forces one carries none of its columns. Base your subscription model on
`AbstractForceableSubscription` instead, track its history, and point
`ISIK_NOTIFICATIONS_SUBSCRIPTION_MODEL` at it:

```python
from isik.django.apps.common.db.history import track_events
from isik.django.apps.notifications.audience.bases import AbstractForceableSubscription

@track_events()
class Subscription(AbstractForceableSubscription):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    kind = models.ForeignKey(NoticeKindPolicy, on_delete=models.PROTECT, related_name="+")
```

It adds `chosen` (whether the person chose this row themselves), `forced`, `forced_enabled`,
`forced_source` and `forced_reason`, constrained to be set together. The person's own `enabled` is kept
beside the forced one, so a release brings their choice back.

```python
from isik.django.apps.notifications.audience.helpers import force_subscription, release_subscription

force_subscription(user, "security_alert", enabled=True, source=STAFF, reason="...")
release_subscription(user, "security_alert")
```

The sources are the [force sources](../features/README.md#force-sources) the features app's settings
take - each saying how long a reason must be, whether staff may lift it, and whether the person may read
it. Who gets a notice is then, per person: **a forced variant > a forced kind > their own variant > their
own kind > the kind's policy**. With the plain base it's exactly the policy and their own rows, as before.

- A `MANDATORY` kind can't be forced: off would silence what must reach them, on means nothing.
- `subscribe()` and `unsubscribe()` refuse a forced row (`SubscriptionForced`, code
  `subscription_forced`), and so do the REST create and delete, with that code in a `409`.
- `ISIK_NOTIFICATIONS["SUBSCRIPTION_FORCED_KIND"]` names a `MANDATORY` application kind of yours,
  raised on each force and release with `{user, kind, variant, forced, enabled}` - never the forced kind
  itself, which a force could silence.
- `isik.E014` refuses a forceable subscription model that isn't tracked.

The inbox: `unread(user)`, and `mark_read(items)` over a queryset of items.
