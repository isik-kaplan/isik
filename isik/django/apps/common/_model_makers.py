"""
Shared plumbing behind every "maker" (feedback's `votes()`/`bookmarks()`/`notes()`/`comments()`,
tags' `tags()`): resolving what a generated model inherits from, building it, and exposing it on
the host via the real Django descriptor for the relation it just created - not a bespoke marker
object - so `Host.<name>.model`/`.config` work for introspection while `host_instance.<name>`
stays Django's own manager. Not tied to either app.
"""

from django.conf import settings
from django.db import models
from django.db.models.signals import class_prepared
from django.db.models.utils import make_model_tuple
from django.utils.module_loading import import_string

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy
from isik.django.apps.common.generated import mark_generated


class DefaultMakerBase(models.Model):
    """Fallback base for a generated model when no `base_model=`/`<SETTING>` applies."""

    created_at = models.DateTimeField(
        auto_now_add=True, help_text=gettext_lazy("When the row was created."), db_comment="When the row was created."
    )
    updated_at = models.DateTimeField(
        auto_now=True, help_text=gettext_lazy("When the row last changed."), db_comment="When the row last changed."
    )

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
        raise TypeError(_("base_model must be an abstract Model subclass, got %(base)r") % {"base": base})
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
            _(
                "related_name=%(related_name)r on %(key)s is already claimed by %(existing)s - pick a "
                "different name for %(owner)s."
            )
            % {"related_name": related_name, "key": key, "existing": existing, "owner": owner_label}
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


def attached(cls, field_cls):
    """Every attribute on `cls` - its own or inherited, a subclass's shadowing its base's - whose
    `.config` is a `field_cls` instance, in the order they're declared."""
    seen = set()
    matches = []
    for klass in cls.__mro__:
        for attr_name, value in vars(klass).items():
            if attr_name in seen:
                continue
            seen.add(attr_name)
            if isinstance(getattr(value, "config", None), field_cls):
                matches.append(value)
    return matches


def resolve_field(obj, explicit, field_cls, verb):
    """
    Returns `explicit` if given, else the single attribute on `obj`'s class whose `.config` is a
    `field_cls` instance. Raises `TypeError` if none match, or if more than one does and
    `explicit` wasn't given (disambiguate with e.g. `field=Post.topics`).
    """
    if explicit is not None:
        return explicit
    cls = obj if isinstance(obj, type) else type(obj)
    matches = attached(cls, field_cls)
    if not matches:
        raise TypeError(
            _("%(model)s is not %(verb)s - attach the matching maker to it first")
            % {"model": cls.__name__, "verb": verb}
        )
    if len(matches) > 1:
        raise TypeError(
            _("%(model)s has multiple %(verb)s fields - pass field= to disambiguate")
            % {"model": cls.__name__, "verb": verb}
        )
    return matches[0]


def build_model(model_name, host_cls, *, fields, base_model, extra_attrs=None, meta_attrs=None):
    """
    Builds the generated model via `type()` against `base_model`'s real `ModelBase` metaclass, so
    any `contribute_to_class`-having value in `fields` (e.g. another maker via `extra_fields=`)
    wires up exactly like a hand-written field, with no special-casing needed here.
    """

    # The base's own Meta - every abstract model has one - so its options reach the generated model,
    # with its constraints and indexes kept beside the maker's own.
    class Meta(base_model.Meta):
        app_label = host_cls._meta.app_label

    meta_attrs = dict(meta_attrs or {})
    for option in ("constraints", "indexes"):
        meta_attrs[option] = [*getattr(base_model.Meta, option, []), *meta_attrs.get(option, [])]
    for option, value in meta_attrs.items():
        setattr(Meta, option, value)
    attrs = {**fields, **(extra_attrs or {}), "Meta": Meta, "__module__": host_cls.__module__}
    return mark_generated(type(model_name, (base_model,), attrs), "maker")


def merge_fields(fields, extra_fields, model_name):
    """`fields` with `extra_fields` added - raising for one that would replace a field the maker
    builds itself, which the rest of the maker relies on being what it built."""
    clashing = sorted(set(fields) & set(extra_fields))
    if clashing:
        raise TypeError(
            _("extra_fields can't replace %(model)s's own %(fields)s - give the extra field another name")
            % {"model": model_name, "fields": ", ".join(clashing)}
        )
    return {**fields, **extra_fields}


# The class_prepared listeners when_registered() is waiting on - held here, as a signal holds its
# listeners weakly, until each hears its model.
_waiting = set()


def when_registered(host_cls, model_ref, callback):
    """
    Calls `callback(model)` once `model_ref` - a class, or `"app_label.Model"` - and `host_cls` are both
    registered and Django has resolved the model's relations to the host, so the reverse accessors
    they add exist. Django resolves a relation in an operation queued when its model is defined;
    one queued earlier for the same model runs first, before the accessor is there - so for a model
    not yet registered this queues its own from `class_prepared`, after Django's.
    """
    apps = host_cls._meta.apps
    key = make_model_tuple(model_ref)

    def queue():
        apps.lazy_model_operation(
            lambda *models: callback(apps.get_registered_model(*key)),
            key,
            (host_cls._meta.app_label, host_cls._meta.model_name),
        )

    try:
        apps.get_registered_model(*key)
    except LookupError:

        def prepared(sender, **kwargs):
            if sender._meta.apps is apps and (sender._meta.app_label, sender._meta.model_name) == key:
                _waiting.discard(prepared)
                class_prepared.disconnect(prepared)
                queue()

        _waiting.add(prepared)
        class_prepared.connect(prepared)
    else:
        queue()


def keyword_list(names):
    """`a=, b=` - option names as a call would spell them."""
    return ", ".join(f"{name}=" for name in names)
