# comments

## comments()
Attaches a per-host comment log (plain text by default), plus `UserCommentMixin` on the User model. Pass `tiptap=True` to store a Tiptap/ProseMirror JSON document instead - see [tiptap.md](tiptap.md) for what that requires.

```python
from isik.django.apps.feedback.comments import UserCommentMixin, comments

class Post(models.Model):
    comments = comments(user_related_name="post_comments")

Post.comments.model                  # generated PostComment model
```

- `comment_min_length`/`comment_max_length` bound plain-text `body`; when `comment_max_length` is set the field is a `CharField` (not `TextField(validators=...)`), so the cap holds at the DB level too, not just in `full_clean()`.
- `votes()` isn't attached automatically - opt in with `extra_fields={"votes": votes(user_related_name=...)}` to make comments themselves voteable.

## Threads and edits

```python
comments = comments(user_related_name="post_comments", threaded=True, max_depth=3, track_edits=True)
```

- `threaded=True` adds `parent` - a comment it replies to, null for a top-level one - and
  `comment.replies`. Deleting a comment deletes its replies.
- `max_depth` caps how deep replies go (top-level comments are depth 0); unset, they go on. Depth is
  counted by walking up the parents when a reply is made - nothing stores it.
- `track_edits=True` adds `edited_at`, stamped by `edit_comment()` and the `PATCH` action.
- A model you write gets the same from having the fields - see [your own models](../your_own_models.md).

## UserCommentMixin
Mix into your User model to comment on anything with `comments()` attached.

```python
class User(UserCommentMixin, AbstractUser):
    pass

user.comment(post, "nice post")
post.comments.all()                  # every comment, any author
user.reply(comment, "agreed")        # on comment's target, within max_depth
user.edit_comment(comment, "edited") # its author only - PermissionDenied otherwise
user.delete_comment(comment)         # its author only
```

## Naming the generated model

The generated model is `<Host><Attr>Comment` by default (`Post.comments` -> `PostCommentsComment`). Pass `model_name=` to name it yourself. It also names the table and constraints, so settle it before the first migration.

```python
comments = comments(user_related_name="post_comments", model_name="PostComment")
```
