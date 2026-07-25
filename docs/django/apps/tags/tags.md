# tags

## tags()
Attaches a per-host tag pool plus a M2M through-table, and mixes in the ability to manage it via `TaggableMixin`. Unlike the feedback makers, tags are shared: any host attaching the same `tags()` field pulls from one pool, deduped by `name`.

```python
from isik.django.apps.tags.tags import TaggableMixin, tags

class Post(TaggableMixin, models.Model):
    topics = tags(related_name="posts_with_topic")

post.add_tag("python")
post.set_tags(["python", "django"])
post.tag_names()                     # ["python", "django"]
post.topics.all()                    # real Django M2M manager
Post.topics.model                    # generated PostTopicsTag model
Post.topics.through                  # generated PostTopicsObjectTag through model
```

- `related_name` is required (Tag -> host reverse accessor, `tag.<related_name>`). Two `tags()` on the same host default to the same `target_related_name` ("tags") - give at least one a distinct value or it raises at class-definition time.
- `normalize` (e.g. `str.lower`) is off by default - `"Python"` and `"python"` are different tags unless you opt in.
- A host can attach `tags()` more than once - pass `field=Post.topics` to disambiguate when there's more than one.

## TaggableMixin
Mix into a host model to get `add_tag`/`remove_tag`/`set_tags`/`tag_names` verbs for any `tags()` attached to it.

```python
post.add_tag("python", field=Post.topics)   # field= only needed with >1 attachment
```

## TagManager / TagQuerySet
The generated Tag model's default manager. `get_tag(name)` get-or-creates by name, applying `normalize=` and the `name` field's validators first (`create()` bypasses `full_clean()`, so this is the actual validation choke point). `from_list(names)` does the same for a whole list; `to_list()` on the resulting queryset returns plain name strings.

```python
Post.topics.model.objects.get_tag("python")                     # get-or-create a single tag
Post.topics.model.objects.from_list(["python", "django"]).to_list()
```

- `create()` is filter-then-create rather than an atomic `get_or_create()` - a race that slips two inserts past the filter check still recovers via the `name` unique constraint instead of raising `IntegrityError`.
