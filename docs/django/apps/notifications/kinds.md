# Kinds

A kind is one thing worth telling someone: `invoice_voided`, `membership_removed`. It's declared in code,
and the declaration is the source of truth - after every `migrate`, each declared kind's
`NoticeKindPolicy` row is written or brought up to date (a `post_migrate` handler, never a data
migration, so your migrations stay what `makemigrations` writes).

## The vocabulary: `declare_kind()`

Every kind a project has is declared in one place - a module someone reviews - and its name is then handed
around as the object, never retyped as a string:

```python
# notices.py
PARCEL_FLAGGED = declare_kind("parcel_flagged", variants=("lost", "damaged"))
REMINDER = declare_kind("reminder", variants=("daily", "weekly"), audience=Subscribers, policy=Policy.DEFAULT_OFF)
```

`declare_kind()` returns a `KindName` - a string, so anything storing it stores the name - carrying the
variants it declares. A kind written by a trigger gets its behavior from `notifies()`, given that name; one
the application raises gets it right there, from `audience` and `policy`. `isik.E014` holds the two steps
to each other: a declared kind nothing writes, a `notifies()` writing other variants than its declaration
names, or a name declared twice, is refused. A one-file project may give `notifies()` a bare name instead,
declaring the kind there and then - unless `REQUIRE_DECLARED_KINDS` is on, which refuses one.

An audience taking no arguments is given as its class - `audience=Subscribers` - and one that needs them
as an instance, `Field("owner")`; a class that needs arguments fails at import, naming them.

## What a person reads: labels and messages

A client built from the API alone knows nothing of your kinds, so each says what it is, lazy-translated
and sent in the request's language:

```python
PARCEL_FLAGGED = declare_kind(
    "parcel_flagged",
    variants=("lost", "damaged"),
    label=_("Parcel problems"),                              # what the preference is called
    description=_("When a parcel you sent is lost or damaged."),
    title=_("A parcel was flagged"),
    message=_("Your parcel %(tracking)s was reported %(status)s."),
)
```

`title` and `message` are what an inbox item reads as: a `%`-format over the notice's snapshot, or a
callable given it and the item's variant - `message=lambda data, variant: ...`, one sentence per variant -
rendered when the item is read - so a client shows a sentence with no switch per kind.
An item whose snapshot lacks what the text names - older than the text - reads as no message rather than
failing. `notifies()` given a bare name takes the same four.

`isik.E016` (`Check.LABELS`) reports a kind with no `label`, and an inbox kind with no `message`; set its
severity, or turn it off, in `ISIK_CHECKS` like any other.

**Run `makemessages` after declaring text, and clear the fuzzy entries it writes.** For a new string it
guesses a translation from a similar existing one and marks it `#, fuzzy` - another sentence's meaning,
which a catalog compiled without refusing fuzzies would show your users. Translate each new entry yourself,
and have your catalog check refuse a fuzzy one.

## Trigger-sourced: `notifies()`

On the model the kind watches:

```python
@notifies(
    PARCEL_FLAGGED,
    on=pgtrigger.Insert | pgtrigger.Update,             # required - Insert, Update, Delete, or several
    condition=None,                                      # a pgtrigger condition, the trigger's WHEN
    variants={"lost": pgtrigger.Q(new__status="lost"), "damaged": pgtrigger.Q(new__status="damaged")},
    snapshot=["status", "sender"],                       # the only columns the notice carries
    previous=False,                                      # True: the same columns as they were, on an update
    audience=Field("sender"),
    policy=Policy.DEFAULT_OFF,
    scope=Scope.SINGLE_PARTITION,                        # default
    channels=("inbox", "email", "webhook"),              # default: every CHANNELS one
    partition=None,                                      # a resolver - see partitions.md
    sensitive=None,                                      # {field: SensitiveSnapshotField(reason=...)}
)
class Parcel(BaseModel):
    ...
```

It registers an AFTER row trigger, `notify_<kind>`, that inserts the notice. Run `makemigrations` - the
trigger is in your app's migration, like any pgtrigger trigger.

- **`on` has no default.** A kind writing a notice on every save of its row is a choice to make, not one
  to fall into.
- **`snapshot` is an allow-list, and it's required to say what leaves the row.** A column it doesn't
  name never reaches the ledger - a secret column on the model stays where it is. The notice's
  `snapshot` holds each named field's column (a foreign key's id under the field's name).
- **A snapshot field that looks secret** - its name says password, secret or token - is warned about
  (`isik.W005`), the snapshot being copied into the ledger and every channel. Carry one only with a
  reason, one field at a time: `sensitive={"api_key_last4": SensitiveSnapshotField(reason="...")}`, an
  exemption listed by `manage.py exemptions` like any other.
- **`condition`** decides whether the trigger runs at all. Leave the database-kept `updated_at` and
  `version` out of it - use isik's `AnyChange` and its siblings (`isik.django.apps.common.db.conditions`),
  which `isik.E013` holds every condition to.
- **`variants`** - one notice per variant whose own condition holds (`None` for always). A kind without
  variants writes one notice with no variant. Subscriptions, endpoints and inbox filters can then name
  one variant, or none for every one.
- The notice carries the change's pghistory context (`pgh_context`), the same one its event rows carry.
- A **retired** kind's trigger writes nothing.

## Application-sourced: `raise_notice()`

For something no row change shows - a password reset, a reminder - declared with its audience and policy
(`REMINDER` above):

```python
raise_notice(REMINDER, variant="daily", subject=invoice, snapshot={"due": "2026-11-01"}, partition=None)
```

`raise_notice()` writes the notice in the current transaction, so it's written only if that commits.
It refuses a kind that isn't application-sourced, a variant the kind doesn't declare, and a retired
kind.

## Recorded: `record_email()`

For an email that has to go out now, sent its own way - a password reset, a sign-in code, allauth's
mail - and that the ledger only records, so "did it go out" has one answer for every email. Nothing of
it is queued, retried or rendered by isik.

```python
from isik.django.apps.notifications.kinds import Source, declare_kind
from isik.django.apps.notifications.pipeline import record_email

PASSWORD_RESET = declare_kind("password_reset", source=Source.RECORDED)

record_email(
    PASSWORD_RESET,
    address=email,
    send=lambda: send_mail(subject, text, sender, [email], connection=connection),
    user=user,           # optional: who it went to
    about=None,          # optional: the row it's about - never the email's Subject:
    partition=None,      # where `user` lives, in a partitioned project
)
```

A recorded kind is mandatory, sent on email alone and has no audience - `declare_kind()` refuses any of
those. `raise_notice()` refuses it, a delivery of it is never retried by hand, and no template is
checked for it.

**A sender that retries** - a Celery task with `autoretry_for`, say - passes `key=`, the same on every
try (the task's `request.id` is): each try adds its attempt to the one delivery, which ends sent or
abandoned by the last, instead of recording an email per try. Between a failed try and the next, it reads
abandoned - nothing of isik's will try it again.

**`send()` comes first, and nothing after it can stop the email.** Only what `send()` raises is raised,
once it's recorded. A kind that isn't a declared recorded one, a variant it doesn't declare, a retired
kind, a ledger that can't be written - each is logged, and the email goes out unrecorded: account
recovery never depends on the ledger. Test the call sites so a mistake there doesn't hide in the logs.

The record - a notice already fanned out, an email delivery sent or abandoned, its attempt per
`RECORD_ATTEMPTS` with `triggered_by="record_email"` - is written after `send()`, never before, so no
worker ever finds it waiting and sends it again. A process killed mid-`send()` leaves no record. The
attempt keeps where the email went (`{"to": address}`), never the message - no reset link is kept.

## Retiring a kind

Nothing is deleted: notices outlive their kind. `manage.py notices --retire <kind>` stamps the policy
row's `retired_at` - the trigger then writes nothing, `raise_notice()` refuses it, and `isik.W004`
reports a kind still declared after it was retired, so its declaration gets removed. A policy row no
longer declared is listed by `manage.py notices`.

## A kind with no policy row

A trigger writes a notice only while its kind's policy row is there and not retired - so a data migration
writing a watched row, before the ledger's tables exist or the mirror has written the row (it does, after
`migrate`), writes no notice and fails nothing: a migration isn't something that happened. Needing no
dependency on the ledger, such a migration runs in any order. Because a missing row is otherwise silent,
`isik.W009` reports a declared kind the database has none for - shown once by the `migrate` that writes it.

## A kind's push

What a kind's push carries, on the [push channel](channels.md#push), is its `push=PushContent(...)` - on
`notifies()` for a trigger-sourced kind, on `declare_kind()` for an application one:

```python
from isik.django.apps.notifications.kinds import PushContent, PushDataField

@notifies(
    INVOICE_VOIDED,
    ...,
    push=PushContent(
        data={"number": PushDataField(reason="the app opens the invoice by its number, which names no one")},
        collapse="subject",
        ttl=timedelta(hours=6),
    ),
)
```

- `private=True` - its label alone, as the title: nothing of the notice reaches a lock screen, or Apple's or
  Google's servers, beyond what kind of thing happened.
- `data` - snapshot fields the app gets beside the notice's kind, variant and subject, each with a
  `PushDataField(reason=...)`: what's in a push leaves the project. A field the snapshot doesn't carry is
  refused at startup.
- `collapse` - `"kind"` or `"subject"`: a later push of the kind (about the same subject) replaces one not
  yet seen, rather than stacking.
- `ttl` - how long the service keeps trying a phone that's off; None leaves it to the service.
- `badge=True` - the app's badge, set to the user's unread inbox items.

**Push turned on by deployment.** A kind leaving `channels` out is sent on every channel `CHANNELS` turns
on, push among them where it is - so it needs nothing. A kind naming its channels names push only where
`CHANNELS` has it, since naming a channel that isn't on is refused at startup:

```python
from isik.django.apps.notifications import conf

PUSH = ("push",) if "push" in conf.setting("CHANNELS") else ()

@notifies(TOKEN_CREATED, ..., channels=("inbox", *PUSH), push=PushContent(private=True))
```

## What the checks refuse

`isik.E014`, at startup:

- a kind declared twice
- a policy that isn't a `Policy`
- a single-partition kind whose audience spans partitions; a cross-partition one with no partitions
- a kind sent on a channel `CHANNELS` doesn't turn on
- a channel `CHANNELS` turns on that can't be loaded - a broken `notifications` extra, say - found at
  startup rather than by the first fan-out, which would set every notice aside
- a kind declared on a ledger model or a pghistory event model - a loop, or a copy of what pghistory wrote
- a snapshot field the model has no column for, or an audience reading a field the snapshot doesn't carry
- a reason given for a snapshot field the snapshot doesn't carry
- a partition resolver that doesn't fit the model, or none at all once partitions are configured
- an email kind without its templates, while one of isik's senders sends the email
- a trigger-sourced kind with no trigger registered
- a push carrying a field the snapshot doesn't, or a kind sent on push with no `PUSH` credentials
