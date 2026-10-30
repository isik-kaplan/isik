"""
What a setting is for a holder, and why - `effective(holder, name)`:

1. **forced** - staff (or another force source) forced it: the forced value, whatever else holds.
2. **feature_missing** / **limited** - the setting needs a feature the holder lacks: its default (`Default()`),
   or the holder's own value clamped to a bound (`AtMost(n)` / `AtLeast(n)`) - `limited` when the clamp moved it.
3. **own** - the holder's own choice; **default** - they've made none.

`has_feature(holder, feature)` answers from the same cache, kept on the holder instance for as long as it lives -
one request, say - so a list of rows asks the database once per holder, and not at all for what the caller loaded
with it: its overrides row by `select_related`, its grants by `prefetch_related` - each with its feature.
`effective_expression(name)` gives the same answer in SQL, for annotating and filtering holders.
"""

from contextlib import nullcontext
from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.functions import Coalesce, Greatest, Least, Now
from django.utils import timezone

from isik._internal.translation import gettext as _
from isik.django.apps.features import conf, declarations
from isik.django.apps.features.swapping import get_grant_model, get_holder_model, get_overrides_model


ORIGINS = ("forced", "feature_missing", "limited", "own", "default")
# Where a holder's cache lives on its instance.
CACHE = "_isik_features"


@dataclass(frozen=True)
class Effective:
    """A setting's value for a holder, `origin` saying why it's that - and, for a forced one, who forced it and why."""

    value: object
    origin: str
    source: str = None
    reason: str = None


def in_holder_schema():
    """`IN_HOLDER_SCHEMA` entered - where grants and overrides live - or nothing, when it's unset."""
    hook = conf.setting("IN_HOLDER_SCHEMA")
    return hook() if hook is not None else nullcontext()


def live_grants(now=None):
    """The grants in force at `now` (now, unless given): started, not ended, not revoked by then."""
    now = now or timezone.now()
    return (
        get_grant_model()
        ._default_manager.filter(starts_at__lte=now)
        .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
        .filter(models.Q(revoked_at__isnull=True) | models.Q(revoked_at__gt=now))
    )


def features_at(holder, when=None):
    """The names of the features in force for `holder` at `when` (now, unless given) - past or to come, as the
    grants now say."""
    return set(live_grants(when).filter(holder=holder).values_list("feature__name", flat=True))


def _cache(holder):
    """`holder`'s cache - filled a key at a time, so a read that needs no feature asks nothing of the grants."""
    return holder.__dict__.setdefault(CACHE, {})


def _held(holder):
    cache = _cache(holder)
    if "features" not in cache:
        cache["features"] = _prefetched_features(holder)
    if cache["features"] is None:
        with in_holder_schema():
            cache["features"] = features_at(holder)
    return cache["features"]


def _prefetched_features(holder):
    """
    The features in force for `holder` from the grants the caller loaded - `prefetch_related` of its grants, each
    with its feature (`select_related("feature")` in the `Prefetch`'s queryset) - or, where none were loaded so,
    None: a grant's feature read one at a time would be a query each. An unsaved holder holds none.
    """
    if holder._state.adding:
        return set()
    for loaded in getattr(holder, "_prefetched_objects_cache", {}).values():
        if loaded.model is get_grant_model():
            if not all("feature" in grant._state.fields_cache for grant in loaded):
                return None
            now = timezone.now()
            return {grant.feature.name for grant in loaded if _in_force(grant, now)}
    return None


def _in_force(grant, now):
    return grant.starts_at <= now and all(end is None or end > now for end in (grant.expires_at, grant.revoked_at))


def _overrides(holder):
    cache = _cache(holder)
    if "overrides" not in cache:
        cache["overrides"] = _loaded_overrides(holder)
    if cache["overrides"] is _UNLOADED:
        with in_holder_schema():
            cache["overrides"] = get_overrides_model()._default_manager.filter(holder=holder).first()
    return cache["overrides"]


# An overrides row the caller didn't load - distinct from None, a holder loaded as having none.
_UNLOADED = object()


def _loaded_overrides(holder):
    """
    `holder`'s overrides row as the caller loaded it - `select_related` of its overrides, None when it found no
    row - or `_UNLOADED`. None, too, where there's no overrides model or the holder isn't saved.
    """
    overrides_model = get_overrides_model()
    if overrides_model is None or holder._state.adding:
        return None
    for relation in _overrides_relations(holder):
        if relation.cache_name in holder._state.fields_cache:
            return holder._state.fields_cache[relation.cache_name]
    return _UNLOADED


def forget(holder):
    """
    Drops `holder`'s cache, so the next read asks the database - after a write, say. What the caller loaded with
    it goes too: its overrides row and its grants, as they were before the write.
    """
    holder.__dict__.pop(CACHE, None)
    for relation in _overrides_relations(holder):
        holder._state.fields_cache.pop(relation.cache_name, None)
    prefetched = getattr(holder, "_prefetched_objects_cache", {})
    for name, loaded in list(prefetched.items()):
        if loaded.model is get_grant_model():
            del prefetched[name]


def _overrides_relations(holder):
    return [relation for relation in holder._meta.related_objects if relation.related_model is get_overrides_model()]


def held_features(holder):
    """The names of the features in force for `holder`."""
    return set(_held(holder))


def has_feature(holder, feature):
    """Whether `feature` (a name or `FeatureName`) is in force for `holder`."""
    return str(feature) in _held(holder)


def overrides_of(holder):
    """`holder`'s overrides row, or None while it has none."""
    return _overrides(holder)


def own_value(holder, setting, overrides):
    """`holder`'s own value of `setting` - on the holder, or on its overrides row - or None while it has made none."""
    if isinstance(setting.own, declarations.OnHolder):
        return getattr(holder, setting.own.field)
    return getattr(overrides, setting.name) if overrides is not None else None


def effective(holder, name):
    """The `Effective` value of the setting `name` for `holder` - see the module's docstring."""
    return resolved(holder, declarations.settings[name])


def resolved(holder, setting, features=None):
    """
    `setting`'s `Effective` value for `holder` were `features` the ones in force - or, unless given, those in force
    now, asked for only when the setting needs one.
    """
    name = setting.name
    overrides = overrides_of(holder)
    if overrides is not None and getattr(overrides, f"{name}_forced"):
        return Effective(
            getattr(overrides, f"{name}_forced_value"),
            "forced",
            getattr(overrides, f"{name}_forced_source"),
            getattr(overrides, f"{name}_forced_reason"),
        )
    own = own_value(holder, setting, overrides)
    value = setting.default if own is None else own
    chosen = "default" if own is None else "own"
    if setting.feature is None or setting.feature in (_held(holder) if features is None else features):
        return Effective(value, chosen)
    if isinstance(setting.without_feature, declarations.LOCKING):
        return Effective(setting.without_feature.locked(setting.default), "feature_missing")
    limited = setting.without_feature.apply(value)
    return Effective(limited, "limited" if limited != value else chosen)


class ResolvedSetting:
    """
    The setting `name` on the holder, at its `OnHolder(resolved_as=...)`: read, its effective value; set, the
    holder's own value - saved as the holder is, or written with `set_setting()`.
    """

    def __init__(self, name):
        self.name = name

    def __get__(self, holder, owner=None):
        if holder is None:
            return self
        return effective(holder, self.name).value

    def __set__(self, holder, value):
        setattr(holder, declarations.settings[self.name].own.field, value)


def resolve_on_holder():
    """Puts each `OnHolder(resolved_as=...)` setting's `ResolvedSetting` on the holder model - once it's loaded."""
    conf.import_settings_module()
    holder = get_holder_model()
    fields = {field.name for field in holder._meta.get_fields()} | {
        field.attname for field in holder._meta.concrete_fields
    }
    for setting in declarations.settings.values():
        name = getattr(setting.own, "resolved_as", None)
        if name is None:
            continue
        if name in fields:
            raise ImproperlyConfigured(
                _("%(setting)s resolves as %(model)s.%(name)s, which is a field of its - name another attribute.")
                % {"setting": setting.name, "model": holder._meta.label, "name": name}
            )
        setattr(holder, name, ResolvedSetting(setting.name))


def effective_expression(name):
    """
    `effective(holder, name).value` as SQL over the holder model - for `annotate()` and `filter()`. Each value it
    names is typed as the setting's field, so a text setting's column and its default are one type.
    """
    setting = declarations.settings[name]
    overrides = get_overrides_model()._default_manager.filter(holder=models.OuterRef("pk"))
    forced = overrides.filter(**{f"{name}_forced": True})
    if isinstance(setting.own, declarations.OnHolder):
        own = models.F(setting.own.field)
    else:
        # One row at most: a holder has one overrides row.
        own = models.Subquery(overrides.values(name))
    value = Coalesce(own, models.Value(setting.default, output_field=setting.field))
    branches = [models.When(models.Exists(forced), then=models.Subquery(forced.values(f"{name}_forced_value")))]
    if setting.feature is not None:
        grants = (
            get_grant_model()
            ._default_manager.filter(
                holder=models.OuterRef("pk"),
                feature__name=setting.feature,
                revoked_at__isnull=True,
                starts_at__lte=Now(),
            )
            .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=Now()))
        )
        bound = setting.without_feature
        if isinstance(bound, declarations.LOCKING):
            missing = models.Value(bound.locked(setting.default), output_field=setting.field)
        else:
            clamp = Least if isinstance(bound, declarations.AtMost) else Greatest
            missing = clamp(value, models.Value(bound.bound))
        branches.append(models.When(~models.Exists(grants), then=missing))
    return models.Case(*branches, default=value)
