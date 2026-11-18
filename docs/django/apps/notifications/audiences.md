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

### Who could ever get a kind: `could_include()`

`recipients()` answers for one notice. `could_include(user)` answers before there is one: could this
person ever be among the recipients? `True`, `False`, or `None` - "can't tell". It's what the preferences
show (`in_audience` on each kind) and what a subscription is refused by: setting one for a kind whose
audience answers `False` is refused, since the fan-out would never read it. `Subscribers` answers `True` -
subscribing is how a person joins it - and `Field` and an audience saying nothing answer `None`, which
refuses nothing.

It's asked for every kind a person sees at once, so it answers from the user row alone, never with a query
of its own. A role on the row is read off it:

```python
class Operators(Audience):
    def recipients(self, notice):
        return get_user_model().objects.filter(is_staff=True)

    def could_include(self, user):
        return user.is_staff
```

What the row doesn't hold, `annotations()` puts on it - `{name: expression}`, as `QuerySet.annotate()`
takes them. isik loads the person once with every visible kind's audiences' annotations together, then asks
each audience, so the preferences ask one query for this however many kinds and audiences there are, and
none when no audience annotates:

```python
from django.db.models import Exists, OuterRef


class Owners(Audience):
    def recipients(self, notice):
        return get_user_model().objects.filter(memberships__role="owner")

    def annotations(self):
        from apps.organizations.models import Membership

        return {"owns_something": Exists(Membership.objects.filter(user=OuterRef("pk"), role="owner"))}

    def could_include(self, user):
        return user.owns_something


class InstallationHolders(Audience):
    # Who holds an installation changes with what's installed now - None, and it refuses nothing.
    def could_include(self, user):
        return None
```

A name means one thing on the row: give it from one audience. An audience on many kinds is the same
audience on each - the same class, its `str()` the same - and gives its annotations once; two different
ones giving one name are refused at startup (`isik.E014`). Name a configurable audience's annotations by
what configures it, as `__str__` does.

A subscription written before its audience said `False` - by a person it never included - stays: the
fan-out asks the audience first, so it's never read and changes nothing they get, and deleting it still
works. Only writing one again is refused.

The answer is the kind's, every variant at once: an audience is asked who a kind could reach, not a variant.

`could_reach(user, names)` (`isik.django.apps.notifications.audiences`) is the same answer for your own
views - `{name: True, False or None}`, `None` for a recorded kind - in that one query. A `KINDS_VISIBLE`
hiding the kinds a person could never get is:

```python
from isik.django.apps.notifications.audiences import could_reach
from isik.django.apps.notifications.kinds import kinds


def kinds_visible(request):
    names = list(kinds())
    return [name for name, reach in could_reach(request.user, names).items() if reach is not False]
```

## Policies

Each kind has a `Policy` on each channel, in its `channels` (see
[kinds.md](kinds.md#the-policy-on-each-channel)):

| policy | who gets it on that channel |
|---|---|
| `MANDATORY` | everyone the audience names - subscriptions are ignored |
| `DEFAULT_ON` | everyone the audience names, but those who turned it off |
| `DEFAULT_OFF` | only those the audience names who turned it on |
| `NEVER` | no one - the kind never goes out on it |

The audience and the subscriptions are read once per notice, and each channel takes its own policy over
them. Webhook endpoints are separate: on a kind whose webhook policy is `MANDATORY`, an endpoint gets every
notice of it (and its variant) its `EndpointKind` rows ask for, whoever the audience is.

## Subscriptions

A subscription is a deviation from the policy - so a newly declared kind reaches everyone it should
with nothing to backfill. One row per user, kind, variant and channel: no variant covers every one, and no
channel every channel the kind leaves to the person.

```python
from isik.django.apps.notifications.audience.helpers import subscribe, unsubscribe

subscribe(user, "parcel_flagged", "lost")                       # on, for one variant, on every channel
subscribe(user, "parcel_flagged", enabled=False)                # off, everywhere - replacing the named ones
subscribe(user, "parcel_flagged", channel=Channel.EMAIL, enabled=False)  # off by email alone
unsubscribe(user, "parcel_flagged")                             # back to the policy
unsubscribe(user, "parcel_flagged", channel=Channel.EMAIL)      # back to it by email
```

One row a person's (variant, channel) at most: a row covering another - naming no variant, or no channel,
where the other names one - can't sit beside it for the same user and kind, nor two rows covering part of
each other (`("lost", every channel)` and `(every variant, "email")`). The database refuses it (`VariantGuard`, under an advisory lock so two
writers can't both pass), and refuses a kind that doesn't exist and a variant the kind doesn't declare -
so a `kind` key without a database constraint, one reaching across schemas, is still checked.
The guard is the trigger `guard_variant` on each kind-holder, and `protect_policy_delete` on
`NoticeKindPolicy` keeps a kind from ever being deleted - so a `kind` key with no constraint of its own
can't dangle. Both names are stable: a project may name them as what guards its keys.
`subscribe()` removes the rows it covers first, in one transaction; naming one beside a row covering it, or
part of it, is refused - `unsubscribe()` that one first. The guard refuses a row naming a channel the kind
leaves no choice on, too. Endpoint kinds and inbox items are held to declared variants the same way.

A channel whose policy is `MANDATORY` or `NEVER` has no choice to record: `subscribe()` naming it is refused
(`ChannelNotChosen`, code `channel_not_chosen`), and so is one naming no channel for a kind leaving no
choice on any (`KindIsMandatory`, code `kind_mandatory`) - so no row says a person turned off what they
can't. A kind with no policy row is
refused too, with a `ValueError`. Given a policy row rather than a name, `subscribe()` uses it without
reading it again. Each refusal is a
`SubscriptionRefused` carrying its `code`.

## Forcing one person's subscription

Staff turning a kind back on (or off) for one person, which they then can't change, is an optional model
base - a project that never forces one carries none of its columns. Base your subscription model on
`AbstractForceableSubscription` instead, track its history, and name it as
`ISIK["DJANGO"]["NOTIFICATIONS"]["SUBSCRIPTION_MODEL"]`:

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
it. Who gets a notice is then, per person and channel: **a forced variant > a forced kind > their own variant > their
own kind > the kind's policy**. With the plain base it's exactly the policy and their own rows, as before.

- A channel whose policy is `MANDATORY` or `NEVER` can't be forced: off would silence what must reach them,
  on means nothing. `force_subscription()` and `release_subscription()` take `channel=` as `subscribe()` does.
- `subscribe()` and `unsubscribe()` refuse a forced row (`SubscriptionForced`, code
  `subscription_forced`), and so do the REST create and delete, with that code in a `409`.
- `ISIK["DJANGO"]["NOTIFICATIONS"]["SUBSCRIPTION_FORCED_KIND"]` names a `MANDATORY` application kind of yours,
  raised on each force and release with `{user, kind, variant, forced, enabled}` - never the forced kind
  itself, which a force could silence.
- `isik.E014` refuses a forceable subscription model that isn't tracked.

The inbox: `unread(user)`, and `mark_read(items)` over a queryset of items.
