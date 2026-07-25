# model_makers

Shared plumbing behind isik's model-generating "makers" (feedback's `votes()`/`bookmarks()`/
`notes()`/`comments()`, tags' `tags()`): picking what a generated model inherits from, building
it via `type()`, and exposing it on the host through the real Django descriptor the field/relation
just created — not a bespoke wrapper — so `Host.<name>.model`/`.config` support introspection
while `host_instance.<name>` stays Django's own manager/field. Not tied to either app.

## resolve_base_model

Picks a generated model's abstract base: `explicit` if given, else `settings.<setting_name>` (a
dotted path) if set, else `DefaultMakerBase` (an abstract model with just `created_at`/
`updated_at`). Raises `TypeError` if the resolved base isn't an abstract `Model` subclass — a
concrete one would silently become multi-table inheritance instead of failing loudly.

```python
from isik.django.apps.common._model_makers import resolve_base_model

base = resolve_base_model(explicit=None, setting_name="VOTES_BASE_MODEL")
```

## claim_related_name

Registers `related_name` as used on `target_model` (a model class or `"app_label.Model"` string),
raising `ValueError` if a different owner already claimed it on the same model — the same clash
Django's system checks would eventually catch, surfaced at model-definition time instead of
`makemigrations`/`check`.

```python
from isik.django.apps.common._model_makers import claim_related_name

claim_related_name("blog.Post", "votes", "PostVote")
```

## expose / expose_via_reverse_accessor

`expose(host_cls, name, descriptor, *, generated_model, config)` stashes `generated_model`/
`config` onto `descriptor` and re-attaches it under `name` on `host_cls`, so `Host.<name>.model`/
`.config` become available for introspection.

`expose_via_reverse_accessor` does the same for FK-based makers, where the descriptor to enhance
is Django's own reverse accessor — not guaranteed to exist yet at `contribute_to_class` time (e.g.
a maker attached via `extra_fields=` on another generated model, while `host_cls` is still
mid-construction). It defers via `host_cls._meta.apps.lazy_model_operation`, the same
pending-operations queue Django's own FK relations use, so it also works under `isolate_apps`.

```python
from isik.django.apps.common._model_makers import expose_via_reverse_accessor

expose_via_reverse_accessor(Host, "things", "things", generated_model=HostThing, config=config)
```

## resolve_field

Returns `explicit` if given, else the single attribute on `obj`'s class whose `.config` is an
instance of `field_cls`. Raises `TypeError` if none match, or if more than one does and `explicit`
wasn't given — pass an explicit field (e.g. `field=Post.topics`) to disambiguate.

```python
from isik.django.apps.common._model_makers import resolve_field

field = resolve_field(post, None, VotesConfig, "votable")
```

## build_model

Builds the generated model via `type()` against `base_model`'s real `ModelBase` metaclass, so any
`contribute_to_class`-having value in `fields` (e.g. another maker attached through
`extra_fields=`) wires up exactly like a hand-written field, with no special-casing needed here.

```python
from isik.django.apps.common._model_makers import build_model

generated = build_model("PostVote", Post, fields={"user": user_field}, base_model=DefaultMakerBase)
```
