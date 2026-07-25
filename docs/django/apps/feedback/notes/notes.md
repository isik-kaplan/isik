# notes

## notes()
Attaches a per-host note log, plus `UserNoteMixin` on the User model. Unlike `bookmarks()`, this is a log, not a toggle - multiple notes per user per object, each independently editable/deletable, no unique constraint.

```python
from isik.django.apps.feedback.notes import UserNoteMixin, notes

class Post(models.Model):
    notes = notes(user_related_name="post_notes")

Post.notes.model                     # generated PostNote model
```

- `body_max_length` (default unbounded) makes `body` a `CharField(max_length=...)` instead of a bare `TextField` - only `CharField` gets both `full_clean()` validation and a DB-level `varchar(N)` column, so code bypassing `full_clean()` (like `UserNoteMixin.add_note()`'s own `objects.create()`) is still capped.

## UserNoteMixin
Mix into your User model to write notes on anything with `notes()` attached.

```python
class User(UserNoteMixin, AbstractUser):
    pass

note = user.add_note(post, "remember to follow up")
user.notes_on(post)                  # this user's notes on `post`
```
