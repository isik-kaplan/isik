# Features

`isik.django.apps.features` (label `isik_features`) - features granted to a holder, and settings a holder
chooses within what their features allow, which staff can force. The holder is whatever a project says:
the user, in a project where each person has their own plan, or an organization.

```python
INSTALLED_APPS = [..., "isik.django.apps.features"]

ISIK_FEATURES_HOLDER_MODEL = "accounts.Organization"     # default: AUTH_USER_MODEL
ISIK_FEATURES_OVERRIDES_MODEL = "accounts.OrganizationOverrides"
ISIK_FEATURES = {
    "SETTINGS": "accounts.feature_settings",              # the module declaring them
    "GRANT_SOURCES": ("manual", "trial", "subscription"),
}
```

Then `migrate`. Every declared feature is mirrored into its catalog row after each `migrate`, like a
notification kind. A catalog row is keyed by its `id`, with its `name` unique, and a grant stores that `id`.
Everything here takes and answers with names, and finds the row by a join.

## Features

A feature is declared in code, once, and its name handed around as the object:

```python
# accounts/feature_settings.py
from isik.django.apps.features.declarations import declare_feature

WEBHOOKS = declare_feature("webhooks", label=_("Webhooks"), description=_("Send events to your own URLs."))
BETA = declare_feature("beta", label=_("Beta"))
INTERNAL = declare_feature("internal_flag", label=_("Internal"), visible_to_holder=False)
```

A **grant** brings one to a holder, from one of `GRANT_SOURCES`, between two times:

```python
from isik.django.apps.features.operations import grant, revoke
from isik.django.apps.features.resolution import has_feature

grant(org, WEBHOOKS, source="subscription", reference=invoice.number)
grant(org, BETA, source="trial", expires_at=now + timedelta(days=14))
revoke(org, BETA)                         # every grant of it not revoked yet; returns how many
has_feature(org, WEBHOOKS)                # in force now: started, not expired, not revoked
```

Grants are a ledger. A database trigger refuses a delete, a TRUNCATE and every update except setting
`revoked_at` where it was null - a revoke is written once, never undone or redated; a grant after it is a
new row. `has_feature()` and `effective()` cache what a holder holds on the holder object, so a list view
pays one lookup; `forget(holder)` drops it, as every write here does. Each half is asked for only when a
read needs it - a setting with no feature never asks for the grants - and not at all when the caller
loaded it with the holder:

```python
from django.db.models import Prefetch
from isik.django.apps.features.swapping import get_grant_model

Organization.objects.select_related("setting_overrides").prefetch_related(
    Prefetch("feature_grants", queryset=get_grant_model().objects.select_related("feature"))
)
```

That needs a `related_name` on the holder key - redeclare it on your own model; isik's is `"+"`. Load each
grant's feature with it: a grant keys its feature by id, and the names are what a check asks about - grants
loaded without their features are read again, one query per holder, as if none were loaded. Only the
grants in force are counted, and `forget()` drops what was loaded too, so a write is never answered from
rows read before it. An unsaved holder holds nothing and asks nothing.

### Checking one

```python
from isik.django.apps.features.drf import HasFeature
from isik.django.apps.features.guards import requires_feature

class EndpointViewSet(...):
    permission_classes = [IsAuthenticated, HasFeature("webhooks")]   # a 403 without it

guard = requires_feature("webhooks")
guard(org)                                # raises FeatureMissing (code "feature_missing") without it
```

A feature never implies a permission - check both. `isik.E017` refuses a feature that unlocks no setting
and that neither `HasFeature` nor `requires_feature()` checks: a feature nothing reads.

### When a grant starts or ends

A grant crossing its `starts_at` or `expires_at` changes what a holder has with no row written, so
nothing would say so. `sweep_boundaries()` does: it tells each boundary crossed since its last run
(`feature_changed`, and `setting_changed` for each setting that moved), each as of its own moment and in
their order - a trial starting and ending between two sweeps is told as both. What a grant changes as it's
written - one starting now, or already started - `grant()` tells then, so the sweep tells each boundary
once. Its first run only notes where it starts.

Run it from beat - in `celery.py`, before Django is set up:

```python
from isik.django.apps.features.schedule import merge_beat_schedule

merge_beat_schedule(app)              # isik.features.sweep every minute; sweep_every= to change it
```

or from a cron, with `manage.py features --sweep`.

## Settings

A setting is a value a holder chooses, typed by a model field, with a default until they do:

```python
from isik.django.apps.features.declarations import AtMost, OnHolder, declare_setting

declare_setting(
    "max_endpoints", models.PositiveIntegerField(validators=[MaxValueValidator(50)]), 0,
    feature=WEBHOOKS,                    # without it: the default, 0, and the holder can't change it
    label=_("Webhook endpoints"),
)
declare_setting(
    "token_lifetime_days", models.PositiveIntegerField(), 30,
    feature=LONG_LIVED_TOKENS, without_feature=AtMost(90),   # without it: their own, at most 90
    label=_("Token lifetime"),
)
declare_setting(
    "two_factor_required", models.BooleanField(), False,
    label=_("Require two-factor"),
    request_policies=(RecentlyReauthenticated,),             # its change passes these too
)
declare_setting("display_name", models.CharField(max_length=100), "", own=OnHolder("name"), label=_("Name"))
```

- **`without_feature`** - what a setting needing a feature is while the holder lacks it: `Default()` (the
  default, and they can't change it), `Fixed(value)` (that value, and they can't change it - 0 endpoints
  without webhooks, 5 with them), or `AtMost(n)`/`AtLeast(n)`, their own value clamped to a bound.
- **`holder_may_change=False`** - the holder reads it and can't change it over the API; staff set it with
  `set_setting()` or `force_setting()`. `MAY_CHANGE_SETTING` stays the answer for the rest.
- **`own`** - where the holder's own value lives: `OnOverrides()` (a column on the overrides row, the
  default), or `OnHolder("field")`, a field of the holder itself - for a setting already part of the
  holder's own model and API.
- **`OnHolder("field", resolved_as="name")`** - the holder reads the setting at `name` as it's in force,
  and setting `name` sets its own value in `field`:

  ```python
  class Organization(models.Model):
      mfa_policy_own = models.CharField(max_length=20, db_column="mfa_policy")   # its own choice

  declare_setting("mfa_policy", ..., own=OnHolder("mfa_policy_own", resolved_as="mfa_policy"))

  organization.mfa_policy            # what's in force - forced, locked, or its own
  organization.mfa_policy = "totp"   # its own choice, saved with organization.save()
  ```

  `name` is never `field`, nor any other field of the holder: Django's `save()` and `refresh_from_db()`
  read a field through its attribute, and would keep a forced value as the holder's own - which a release
  could then never undo. `db_column` keeps the column where it was. Anything that reads `name` and writes it
  back - a form, a serializer - writes what was in force as the holder's own: write a setting through its
  own field, or `set_setting()`.
- **`visible_to_holder=False`** - kept out of the holder's listing; staff's alone.

Labels and descriptions are lazy-translated text shown to holders - after declaring them, run
`makemessages` and clear the fuzzy guesses it writes (see [kinds](../notifications/kinds.md#what-a-person-reads-labels-and-messages)).

The types the API knows are bool, int, decimal, float, duration, text, and choice - any field with
`choices`.

### The overrides model is the project's

isik ships no overrides model: its columns are the project's settings. Declare it in your own app, track
its history, and point the setting at it:

```python
from isik.django.apps.common.db.history import track_events
from isik.django.apps.features.bases import AbstractOverrides

@track_events()
class OrganizationOverrides(AbstractOverrides):
    """An organization's settings."""
```

`ISIK_FEATURES["SETTINGS"]` is imported before the class is built, and each declared setting adds its
columns: `<name>` (where it's `OnOverrides`), and `<name>_forced`, `<name>_forced_value`,
`<name>_forced_source` and `<name>_forced_reason`, with a constraint that they're all set or none are.
Adding a setting is a `makemigrations` in your app. A row is made on the holder's first write; until then
every setting is its default.

`isik.E017` refuses an overrides model that isn't tracked - who forced a setting, when and why is its
history - and a holder key that cascades.

### What a setting is: `effective()`

```python
from isik.django.apps.features.resolution import effective, effective_expression

effective(org, "token_lifetime_days")
# Effective(value=90, origin="limited", source=None, reason=None)
```

In this order: **forced** (staff's value) → **feature_missing** (the default, for want of the feature) →
**limited** (their own, clamped for want of it) → **own** (their choice) → **default**.
`effective_expression(name)` is the same answer as a database expression, to filter or annotate holders
by: `Organization.objects.alias(lifetime=effective_expression("token_lifetime_days")).filter(lifetime__gt=30)`.

### Changing one

```python
from isik.django.apps.features.operations import (
    force_setting,
    release_everything_forced_by,
    release_setting,
    set_setting,
)

set_setting(org, "token_lifetime_days", 60)           # the holder's own value
force_setting(org, "token_lifetime_days", 30, source=STAFF, reason="...")   # theirs is kept
release_setting(org, "token_lifetime_days")           # their 60 is back
release_everything_forced_by(org, source=ARCHIVED, by_operator=False)   # every force ARCHIVED made: a count
```

`release_everything_forced_by()` lifts every force one source made on a holder, and nothing another made -
so an automation forcing several settings (archiving an organization, say) undoes itself from the row as it
is, never from a list of its forces kept beside it that drifts the first time a setting joins it.

`set_setting()` refuses a forced setting (`SettingForced`, code `setting_forced`), one locked for want of
its feature (`SettingLocked`, `setting_locked`), and a value its field doesn't take (`SettingInvalid`,
`setting_invalid`) - each a `SettingRefused`.

### Force sources

Who or what forces a value is a **force source**, declared once - and what it allows lives on the source,
not on each row. Notifications' forceable subscriptions take the same sources.

```python
from isik.django.apps.common.forcing import declare_force_source

STAFF = declare_force_source("staff", label=_("Staff"), shows_reason_to_holder=True)
REVIEW = declare_force_source("account_review", label=_("Account review"), operator_may_release=False)
AUTOMATED = declare_force_source("automated", reason_floor=5)
```

- `reason_floor` - how long a reason has to be (40 by default: one worth reading).
- `operator_may_release` - False: only the source lifts it - `release_setting(..., by_operator=False)`.
- `shows_reason_to_holder` - whether the holder may read the reason; an internal one stays with staff.

A declared source is its name, as a declared feature is: a force's row stores the name, so
`overrides.theme_forced_source == STAFF` holds. Two sources compare by name alone, so make one only with
`declare_force_source()`, which refuses a name declared twice - never `ForceSource(...)` by hand.

## Signals

- `setting_changed(holder, name, before, after)` - a setting's resolved value moved, whatever moved it: a
  force, a release, the holder's own write, a feature granted, revoked, started or ended. `before` and
  `after` are `Effective`s.
- `feature_changed(holder, feature, held)` - a feature came into force for a holder, or went out of it.

isik sends them and stops. Reaching the holder's people - signing them out when `two_factor_required` is
forced on, say - is a receiver of yours.

## Erasing a holder

Grants and overrides refuse a delete, and their history outlives them, so a cascading holder key would be
refused by the database and leave the history behind - `isik.E017` refuses one. The holder key's
`on_delete` is `ERASE` instead (`isik.django.apps.features.bases.ERASE`): deleting the holder erases its
grants, its overrides and their history in the delete's own transaction, so a delete that fails erases
nothing.

`erase_holder(holder)` does the same without the delete - for an erasure request that keeps the holder.

A project that would rather refuse the delete - so a holder with grants is never deleted by accident -
redeclares the key on its own model with `on_delete=models.PROTECT`, and calls `erase_holder()` before a
delete it means. Only the keys declared `ERASE` are erased.

## Configuration

`ISIK_FEATURES`, every key optional. A hook is a callable or its dotted path.

| key | default | |
|---|---|---|
| `SETTINGS` | `None` | the module declaring the settings |
| `HOLDER` | `None` | `f(request)` - the holder a request is about; `request.user` by default |
| `IN_HOLDER_SCHEMA` | `None` | a context manager entered around every read and write of grants and overrides - where they live |
| `GRANT_SOURCES` | `("manual",)` | the sources a grant may name |
| `SHOW_GRANT_SOURCE` | `False` | whether `GET features/` shows a grant's source and reference |
| `FEATURES_VISIBLE`, `SETTINGS_VISIBLE` | `None` | `f(request, holder)` - the names the views show; every one declared visible by default |
| `MAY_CHANGE_SETTING` | `None` | `f(request, holder, name)` - may this request change it; by default, when the holder is the requester |
| `PEOPLE` | `None` | `f(holder)` - who a notice about the holder reaches (`HolderPeople`); the holder, when it's a user |

## Owning the models

Each model is swappable - `ISIK_FEATURES_FEATURE_MODEL`, `_GRANT_MODEL`, `_SWEEP_MODEL` - on
`AbstractFeature`, `AbstractFeatureGrant` and `AbstractSweepCursor`, with `get_feature_model()` and so on.
The overrides model is always yours. isik's own tables are `isik_feature`, `isik_feature_grant` and
`isik_sweep_cursor`.

**A holder that isn't the user needs a grant model of yours.** isik's own `FeatureGrant` is keyed to
`AUTH_USER_MODEL` - its migration can only name a holder fixed when it was written - and `isik.E017`
refuses it beside another holder:

```python
class OrganizationFeatureGrant(AbstractFeatureGrant):     # its holder key: ISIK_FEATURES_HOLDER_MODEL
    """An organization's grants."""

ISIK_FEATURES_GRANT_MODEL = "accounts.OrganizationFeatureGrant"
```

The migration creating the models you own runs before isik's, which points at them - Django resolves a
swapped model's app to its first migration, and yours may come later:

```python
class Migration(migrations.Migration):
    run_before = [("isik_features", "0001_initial")]
```

## Telling the holder

With notifications installed, declare kinds on the grant or overrides model with `@notifies` and the
`HolderPeople()` audience - the people `PEOPLE` names for the holder the notice's snapshot carries.

See [the REST views](rest.md) for what a client sees.
