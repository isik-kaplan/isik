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

## UserCommentMixin
Mix into your User model to comment on anything with `comments()` attached.

```python
class User(UserCommentMixin, AbstractUser):
    pass

user.comment(post, "nice post")
post.comments.all()                  # every comment, any author
```
