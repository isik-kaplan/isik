"""
Shared plumbing behind every "maker" (feedback's `votes()`/`bookmarks()`/`notes()`/`comments()`,
tags' `tags()`): resolving what a generated model inherits from, building it, and exposing it on
the host via the real Django descriptor for the relation it just created - not a bespoke marker
object - so `Host.<name>.model`/`.config` work for introspection while `host_instance.<name>`
stays Django's own manager. Not tied to either app.
"""

from django.conf import settings
from django.db import models
from django.utils.module_loading import import_string


class DefaultMakerBase(models.Model):
    """Fallback base for a generated model when no `base_model=`/`<SETTING>` applies."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def resolve_base_model(explicit, setting_name):
    """
    Picks the generated model's base: `explicit` if given, else `settings.<setting_name>` (a
    dotted path) if set, else `DefaultMakerBase`. Raises if the resolved base isn't abstract - a
    concrete one would silently become multi-table inheritance instead of failing loudly.
    """
    if explicit is not None:
        base = explicit
    else:
        dotted = getattr(settings, setting_name, None)
        base = import_string(dotted) if dotted else DefaultMakerBase
    if not (isinstance(base, type) and issubclass(base, models.Model) and base._meta.abstract):
        raise TypeError(f"base_model must be an abstract Model subclass, got {base!r}")
    return base


def _model_key(model_ref):
    if isinstance(model_ref, str):
        return model_ref
    return f"{model_ref._meta.app_label}.{model_ref.__name__}"


_claimed_related_names = {}


def claim_related_name(target_model, related_name, owner_label):
    """
    Registers `related_name` as used on `target_model`, raising if a different owner already
    claimed it - the same clash Django's system checks would eventually catch, surfaced
    immediately at model-definition time instead of at `makemigrations`/`check`.
    """
    key = _model_key(target_model)
    claimed = _claimed_related_names.setdefault(key, {})
    existing = claimed.get(related_name)
    if existing is not None and existing != owner_label:
        raise ValueError(
            f"related_name={related_name!r} on {key} is already claimed by {existing} - pick a "
            f"different name for {owner_label}."
        )
    claimed[related_name] = owner_label


def expose(host_cls, name, descriptor, *, generated_model, config):
    """
    Stashes `generated_model`/`config` onto `descriptor` - the real Django descriptor the
    field/M2M just created - then re-attaches it under `name`, so `Host.<name>.model`/`.config`
    are available while `host_instance.<name>` stays Django's own manager.
    """
    descriptor.model = generated_model
    descriptor.config = config
    setattr(host_cls, name, descriptor)
    return descriptor


def expose_via_reverse_accessor(host_cls, name, accessor_name, *, generated_model, config):
    """
    Like `expose()`, but for FK-based makers where the descriptor to enhance is Django's own
    reverse accessor (`accessor_name`), which isn't guaranteed to exist yet at
    `contribute_to_class` time - e.g. when `host_cls` is still mid-construction, as happens for a
    maker attached via `extra_fields=` on another generated model. Django only wires up a FK's
    reverse accessor once the related model finishes `apps.register_model()` (after
    `class_prepared`), so this defers via `host_cls._meta.apps.lazy_model_operation` - the same
    pending-operations queue Django's own FK relations use - instead of reading it back
    immediately. Uses `host_cls._meta.apps` (not the global registry) so this also works under
    `isolate_apps`.
    """

    def _expose(model):
        expose(host_cls, name, getattr(model, accessor_name), generated_model=generated_model, config=config)

    host_cls._meta.apps.lazy_model_operation(_expose, (host_cls._meta.app_label, host_cls._meta.model_name))


def resolve_field(obj, explicit, field_cls, verb):
    """
    Returns `explicit` if given, else the single attribute on `obj`'s class whose `.config` is a
    `field_cls` instance. Raises `TypeError` if none match, or if more than one does and
    `explicit` wasn't given (disambiguate with e.g. `field=Post.topics`).
    """
    if explicit is not None:
        return explicit
    cls = obj if isinstance(obj, type) else type(obj)
    seen = set()
    matches = []
    for klass in cls.__mro__:
        for attr_name, value in vars(klass).items():
            if attr_name in seen:
                continue
            seen.add(attr_name)
            if isinstance(getattr(value, "config", None), field_cls):
                matches.append(value)
    if not matches:
        raise TypeError(f"{cls.__name__} is not {verb} - attach the matching maker to it first")
    if len(matches) > 1:
        raise TypeError(f"{cls.__name__} has multiple {verb} fields - pass field= to disambiguate")
    return matches[0]


def build_model(model_name, host_cls, *, fields, base_model, extra_attrs=None, meta_attrs=None):
    """
    Builds the generated model via `type()` against `base_model`'s real `ModelBase` metaclass, so
    any `contribute_to_class`-having value in `fields` (e.g. another maker via `extra_fields=`)
    wires up exactly like a hand-written field, with no special-casing needed here.
    """
    meta = type("Meta", (), {"app_label": host_cls._meta.app_label, **(meta_attrs or {})})  # pragma: no mutate
    attrs = {**fields, **(extra_attrs or {}), "Meta": meta, "__module__": host_cls.__module__}
    return type(model_name, (base_model,), attrs)
