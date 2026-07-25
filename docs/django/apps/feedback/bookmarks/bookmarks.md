# bookmarks

## bookmarks()
Attaches a per-host bookmark through-table, plus `UserBookmarkMixin` on the User model. Existence of the row *is* the bookmark - no state field needed, one row per `(target, user)` enforced by a DB unique constraint.

```python
from isik.django.apps.feedback.bookmarks import UserBookmarkMixin, bookmarks

class Post(models.Model):
    bookmarks = bookmarks(user_related_name="post_bookmarks")

Post.bookmarks.model                 # generated PostBookmark model
```

- Same configuration knobs as `votes()` - `user_related_name` required, `target_related_name`/`target_name`/`base_model`/`extra_fields` overridable, and the same `field=` disambiguation rule when attached more than once.

## UserBookmarkMixin
Mix into your User model to bookmark anything with `bookmarks()` attached.

```python
class User(UserBookmarkMixin, AbstractUser):
    pass

user.bookmark(post)
user.toggle_bookmark(post)
user.is_bookmarked(post)
user.unbookmark(post)
```
