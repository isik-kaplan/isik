# Your own models

Each maker builds its model for you. When you need one it doesn't build - another primary key, a table
name, more fields, threads under your own rules - write the model yourself and bind it with `model=`.
Everything else keeps working on it: the `User*Mixin` methods, [`with_feedback()`](annotations.md),
the [viewset actions](viewsets.md) and the `generic_*_serializer()` factories.

```python
class Post(BaseModel):
    comments = comments(model="blog.PostComment", max_depth=3)
    votes = votes(model="blog.PostVote")


class PostComment(BaseModel):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="post_comments")
    body = models.TextField()
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies")
    edited_at = models.DateTimeField(null=True, blank=True)


class PostVote(BaseModel):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="post_votes")
    value = models.SmallIntegerField(choices=[(1, "up"), (-1, "down")])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["post", "user"], name="one_post_vote_per_user")]
```

- `model=` is a class or `"app_label.Model"` - a string lets the model come after its host and point
  back at it, as above.
- The key to the host is found on its own - the model's one `ForeignKey` to the host - or named by
  `target_name=` when it has several. Its `related_name` is the host's accessor (`post.comments`), so it
  can't be hidden (`"+"`).
- The model is checked once it's registered, and every problem is raised at once in one
  `ImproperlyConfigured` - startup fails rather than a request.
- Options that describe a model the maker builds - `user_related_name`, `target_related_name`,
  `user_model`, `base_model`, `extra_fields`, `model_name`, and each maker's own (`tiptap`,
  `comment_min_length`, `body_max_length`, `threaded`, ...) - are refused beside `model=` with a
  `TypeError`. `target_name` and `max_depth` go with either.
- A bound model is yours: it isn't marked generated, so isik's documentation check
  (`isik.E010`) asks it for help text like any other model.

## What each maker needs

Every model needs `id` (its primary key), `user` (a `ForeignKey`, to the user model the mixins and
actions sign in with), a `ForeignKey` to the host, and `created_at` (a `DateTimeField`). Beyond that:

| maker | fields | unique together |
|---|---|---|
| `votes()` | `value` - an `IntegerField` holding 1 or -1 | (host key, `user`) |
| `bookmarks()` | - | (host key, `user`) |
| `notes()` | `body` - a `TextField` or `CharField`; `updated_at` | - |
| `comments()` | `body` - a `TextField`, `CharField` or `JSONField`; `updated_at` | - |

"Unique together" is a plain `UniqueConstraint` (no `condition`) or `unique_together`.

Comments switch on more when the model has them:

- `parent` - a nullable `ForeignKey` to the comment model itself: replies, `max_depth`, `reply_count`.
- `edited_at` - a `DateTimeField`: stamped on each edit.

Tiptap validation is the body field's own then - put `TiptapValidator` on it (see [comments/tiptap.md](comments/tiptap.md)).

## Tags

`tags(tag_model=..., through_model=...)` - see [tags.md](../tags/tags.md#your-own-models).
