"""
Changing what a holder has - granting and revoking features, forcing and releasing settings, writing the holder's
own value - and the two housekeeping acts: the boundary sweep and erasing a holder. Each write that moves a
setting's resolved value sends `setting_changed`; each that brings a feature in or out of force,
`feature_changed`.
"""

from contextlib import contextmanager
from datetime import timedelta

import pgtrigger
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.functions import Now
from django.utils import timezone

from isik._internal.translation import gettext as _
from isik.django.apps.common.forcing import checked_force, checked_release
from isik.django.apps.features import conf, declarations
from isik.django.apps.features.bases import ERASE
from isik.django.apps.features.resolution import (
    effective,
    features_at,
    forget,
    held_features,
    in_holder_schema,
    live_grants,
    resolved,
)
from isik.django.apps.features.signals import feature_changed, setting_changed
from isik.django.apps.features.swapping import get_grant_model, get_overrides_model, get_sweep_model


class SettingRefused(ValueError):
    """Why a setting can't be changed as asked. `code` says which refusal it is, for a client."""

    code = "setting_refused"


class SettingForced(SettingRefused):
    """The setting is forced: the holder can't change it until it's released."""

    code = "setting_forced"


class SettingLocked(SettingRefused):
    """The setting needs a feature the holder lacks, and is its default without it - nothing to choose."""

    code = "setting_locked"


class SettingInvalid(SettingRefused):
    """The value isn't one the setting takes - out of its choices or its limits."""

    code = "setting_invalid"


class GrantRefused(ValueError):
    """Why a feature can't be granted - one that isn't declared, or a source that isn't one of GRANT_SOURCES."""


def _setting(name):
    try:
        return declarations.settings[name]
    except KeyError:
        raise SettingRefused(_("No setting called %(name)s is declared.") % {"name": name}) from None


def _checked_value(setting, value):
    try:
        return setting.field.clean(value, None)
    except ValidationError as invalid:
        raise SettingInvalid("; ".join(invalid.messages)) from invalid


@contextmanager
def _telling(holder, names):
    """Sends `setting_changed` for each of `names` whose resolved value the block moved."""
    forget(holder)
    before = {name: effective(holder, name) for name in names}
    yield
    forget(holder)
    for name in names:
        after = effective(holder, name)
        if after.value != before[name].value:
            setting_changed.send(sender=type(holder), holder=holder, name=name, before=before[name], after=after)


def _overrides_row(holder):
    model = get_overrides_model()
    row, _created = model._default_manager.select_for_update().get_or_create(holder=holder)
    return row


def grant(holder, feature, *, source, reference="", starts_at=None, expires_at=None):
    """
    Grants `feature` to `holder` from `source` - in force from `starts_at` (now) until `expires_at` (never). What it
    moves now is told now; a start or an end still to come is the sweep's to tell.
    """
    name = str(feature)
    if name not in declarations.features:
        raise GrantRefused(_("No feature called %(name)s is declared.") % {"name": name})
    if source not in conf.setting("GRANT_SOURCES"):
        raise GrantRefused(_("%(source)s isn't one of GRANT_SOURCES.") % {"source": source})
    with in_holder_schema(), transaction.atomic():
        made = get_grant_model()._default_manager.create(
            holder=holder,
            feature_id=name,
            source=source,
            reference=reference,
            # The database's clock, as created_at's: a grant starting as it's written starts at its writing.
            starts_at=starts_at or Now(),
            expires_at=expires_at,
        )
        made.refresh_from_db()
        if made.starts_at <= made.created_at:
            # In force as it's written: told now. A start still to come is the sweep's.
            forget(holder)
            held = held_features(holder)
            _tell_crossing(holder, name, held - _held_only_by(made), held)
    return made


def _held_only_by(made):
    """{made's feature} where no grant but `made` holds it now - else nothing."""
    others = live_grants().filter(holder=made.holder_id, feature=made.feature_id).exclude(pk=made.pk)
    return set() if others.exists() else {made.feature_id}


def revoke(holder, feature):
    """Revokes every grant of `feature` to `holder` not revoked yet. Returns how many it revoked."""
    name = str(feature)
    with in_holder_schema(), transaction.atomic():
        forget(holder)
        before = held_features(holder)
        revoked = (
            get_grant_model()
            ._default_manager.filter(holder=holder, feature=name, revoked_at__isnull=True)
            .update(revoked_at=timezone.now())
        )
        forget(holder)
        _tell_crossing(holder, name, before, held_features(holder))
    return revoked


def force_setting(holder, name, value, *, source, reason):
    """
    Forces the setting `name` to `value` for `holder` - they can't change it until it's released. Their own value
    is kept and comes back on release. Refused for a source that isn't declared or a reason short of its floor.
    """
    setting = _setting(name)
    declared = checked_force(source, reason)
    value = _checked_value(setting, value)
    with in_holder_schema(), transaction.atomic(), _telling(holder, [name]):
        row = _overrides_row(holder)
        setattr(row, f"{name}_forced", True)
        setattr(row, f"{name}_forced_value", value)
        setattr(row, f"{name}_forced_source", declared.name)
        setattr(row, f"{name}_forced_reason", reason)
        row.save()


def release_setting(holder, name, *, by_operator=True):
    """
    Lifts the force on the setting `name` for `holder`, so their own value decides again. Refused where the force's
    source doesn't let staff lift it and `by_operator` is True. Returns whether one was released.
    """
    _setting(name)
    with in_holder_schema(), transaction.atomic(), _telling(holder, [name]):
        model = get_overrides_model()
        row = model._default_manager.select_for_update().filter(holder=holder, **{f"{name}_forced": True}).first()
        if row is None:
            return False
        checked_release(getattr(row, f"{name}_forced_source"), by_operator=by_operator)
        _unforce(row, name)
        row.save()
    return True


def release_everything_forced_by(holder, *, source, by_operator=True):
    """
    Lifts every force `source` put on `holder`'s settings, and nothing another source forced - so what forced them
    undoes itself from the row as it is, not from a list of its forces kept beside it. Refused where the source
    doesn't let staff lift it and `by_operator` is True. Returns how many it released.
    """
    declared = checked_release(source, by_operator=by_operator)
    with in_holder_schema(), transaction.atomic():
        row = get_overrides_model()._default_manager.select_for_update().filter(holder=holder).first()
        if row is None:
            return 0
        forced = [name for name in declarations.settings if getattr(row, f"{name}_forced_source") == declared.name]
        with _telling(holder, forced):
            for name in forced:
                _unforce(row, name)
            if forced:
                # Saved only for a change: the row's version is the ETag a holder's client holds.
                row.save()
    return len(forced)


def _unforce(row, name):
    for suffix, cleared in (
        ("_forced", False),
        ("_forced_value", None),
        ("_forced_source", None),
        ("_forced_reason", None),
    ):
        setattr(row, f"{name}{suffix}", cleared)


def set_setting(holder, name, value):
    """
    Writes `holder`'s own value of the setting `name`. Refused while it's forced, and while it needs a feature the
    holder lacks and is its default without it; a bounded one is written as chosen, and read clamped.
    """
    setting = _setting(name)
    value = _checked_value(setting, value)
    with in_holder_schema(), transaction.atomic(), _telling(holder, [name]):
        now = effective(holder, name)
        if now.origin == "forced":
            raise SettingForced(_("%(name)s is forced, so it can't be changed.") % {"name": name})
        if now.origin == "feature_missing":
            raise SettingLocked(_("%(name)s needs a feature you don't have.") % {"name": name})
        if isinstance(setting.own, declarations.OnHolder):
            setattr(holder, setting.own.field, value)
            holder.save(update_fields=[setting.own.field])
        else:
            row = _overrides_row(holder)
            setattr(row, name, value)
            row.save()


def sweep_boundaries(now=None):
    """
    Tells what a grant starting or ending since the last sweep moved - no row is written when one does, so nothing
    else would: `feature_changed`, and `setting_changed` for each setting it unlocks whose value moved. Each is told
    as of its own moment, in their order; one at or before its grant was written was told by `grant()`. Run it from
    a beat, or `manage.py features --sweep` from a cron. Returns how many boundaries it found.
    """
    now = now or timezone.now()
    with in_holder_schema(), transaction.atomic():
        cursor = get_sweep_model()._default_manager.select_for_update().first()
        if cursor is None:
            # The first sweep starts the clock: nothing before it was missed, there was nobody to tell.
            get_sweep_model()._default_manager.create(swept_at=now)
            return 0
        since, cursor.swept_at = cursor.swept_at, now
        cursor.save()
        crossings = [
            (getattr(grant, field), grant)
            for field in ("starts_at", "expires_at")
            for grant in get_grant_model()
            ._default_manager.filter(
                models.Q(revoked_at__isnull=True) | models.Q(revoked_at__gt=models.F(field)),
                **{f"{field}__gt": since, f"{field}__lte": now},
            )
            .filter(**{f"{field}__gt": models.F("created_at")})
        ]
        crossings.sort(key=lambda crossing: crossing[0])
        for at, grant in crossings:
            before, after = features_at(grant.holder, at - JUST_BEFORE), features_at(grant.holder, at)
            _tell_crossing(grant.holder, grant.feature_id, before, after)
    return len(crossings)


# How long before a boundary its "before" is read - the grain of a timestamp.
JUST_BEFORE = timedelta(microseconds=1)


def _tell_crossing(holder, feature, before, after):
    """Tells what `feature` going from held in `before` to held in `after` moved - nothing, where it stayed."""
    forget(holder)
    if (feature in before) == (feature in after):
        # Another grant of it still holds - or another started - so nothing changed for the holder.
        return
    feature_changed.send(sender=type(holder), holder=holder, feature=feature, held=feature in after)
    for name in declarations.settings_unlocked_by(feature):
        setting = declarations.settings[name]
        was, now = resolved(holder, setting, before), resolved(holder, setting, after)
        if now.value != was.value:
            setting_changed.send(sender=type(holder), holder=holder, name=name, before=was, after=now)


def erase_holder(holder):
    """
    Removes `holder`'s grants and overrides, and their history - for staff deleting the account, or an erasure
    request. Their rows refuse a delete otherwise; erased, nothing of them outlives the holder.
    """
    with transaction.atomic():
        _erase_of(holder, _holder_models())


def erase_with_holder(sender, instance, **kwargs):
    """
    The holder model's `pre_delete`: erases the holder's rows whose key is `ERASE` - inside the delete's
    transaction, so a delete that fails erases nothing.
    """
    _erase_of(instance, [model for model in _holder_models() if _holder_key(model).on_delete is ERASE])


def _holder_models():
    return [model for model in (get_grant_model(), get_overrides_model()) if model is not None]


def _holder_key(model):
    return model._meta.get_field("holder").remote_field


def _erase_of(holder, holder_models):
    """
    Erases `holder`'s rows of `holder_models` and their history. Every trigger stands aside for these deletes, not
    only the protections: the history's own delete trigger would otherwise write the erased rows back as history.
    """
    with in_holder_schema(), pgtrigger.ignore():
        for model in holder_models:
            _erase(model._default_manager.filter(holder=holder))
    forget(holder)


def _erase(rows):
    """Deletes `rows` and, where their model is tracked, their history."""
    if "pgh_event_model" in dir(rows.model):
        rows.model.pgh_event_model._default_manager.filter(pgh_obj__in=rows).delete()
    rows.delete()
