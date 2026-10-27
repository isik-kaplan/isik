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
- A name is one or more letters, digits, `.`, `-` or `_` by default; `name_validators=` replaces that rule. The empty name is refused.
- A host can attach `tags()` more than once - pass `field=Post.topics` to disambiguate when there's more than one.
- `remove_tag()` normalizes the name as `add_tag()` does.

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

## Naming the generated models

The generated models are `<Host><Attr>Tag` and `<Host><Attr>ObjectTag` by default. Pass `tag_model_name=`/`through_model_name=` to name them yourself. They also name the tables, so settle them before the first migration.

```python
topics = tags(related_name="posts_with_topic", tag_model_name="Topic", through_model_name="PostTopic")
```

## Your own models

`tag_model=` takes a tag model you write - shared by as many hosts as name it - and `through_model=` the
through model too (it needs `tag_model=`, since its tag key points at that model). Each is a class or
`"app_label.Model"`, checked once registered.

```python
class Topic(AbstractTag):           # a unique `name` with the default validators, and TagManager
    pass

class PostTopic(models.Model):
    tag = models.ForeignKey(Topic, on_delete=models.CASCADE)
    post = models.ForeignKey("blog.Post", on_delete=models.CASCADE, related_name="topic_links")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tag", "post"], name="one_post_topic")]

class Post(TaggableMixin, models.Model):
    topics = tags(related_name="posts", tag_model=Topic, through_model=PostTopic)
```

- A tag model needs a unique `CharField` named `name` and a `TagManager` as its default manager -
  `AbstractTag` has both. Give it `_normalize`, a function of a name, to normalize as `normalize=` does.
- A through model needs a `ForeignKey` named `tag` to the tag model, one to the host (or `target_name=`
  naming it), and the two unique together.
- The options that describe a model `tags()` builds are refused beside the one you give: the `name_*`,
  `normalize` and `tag_*` options beside `tag_model=`; `target_related_name` and the `through_*` options
  beside `through_model=`.

## with_tag_usage(queryset, field)

Annotates a tag model's queryset with `usage_count` - how many of `field`'s host rows carry each tag - as
one subquery, so a list of tags costs one query. A pool several hosts share is counted for `field`'s
host only.

```python
from isik.django.apps.tags.tags import with_tag_usage

with_tag_usage(Post.topics.model.objects.all(), Post.topics).order_by("-usage_count")
```
