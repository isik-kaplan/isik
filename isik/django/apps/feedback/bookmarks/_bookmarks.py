"""bookmarks() maker + UserBookmarkMixin - see `bookmarks` for usage."""

from django.db.models import Exists, Value

from isik._internal.translation import gettext_lazy, lazy_format
from isik.django.apps.common._model_makers import resolve_field
from isik.django.apps.feedback._attachment import FeedbackAttachment


class _BookmarksField(FeedbackAttachment):
    """Descriptor returned by `bookmarks()` - see `bookmarks` for the public docstring."""

    maker = "bookmarks()"
    noun = "Bookmark"
    base_setting = "FEEDBACK_BOOKMARKS_BASE_MODEL"
    default_related_name = "bookmarks"
    unique_per_user = True

    def descriptions(self, host_cls):
        return (
            (
                lazy_format("The %(target)s bookmarked.", target=host_cls.__name__),
                f"The {host_cls.__name__} bookmarked.",
            ),
            (gettext_lazy("Who bookmarked it."), "Who bookmarked it."),
        )

    def own_annotations(self, signed_in, user):
        rows = self.rows()
        mine = Exists(rows.filter(user=user)) if signed_in else Value(False)
        return (("count", self.count(rows)), ("mine", mine))


def bookmarks(
    *,
    model=None,
    user_related_name=None,
    target_name=None,
    target_related_name=None,
    user_model=None,
    base_model=None,
    extra_fields=None,
    model_name=None,
):
    """
    Attaches a per-host-model bookmark through-table, plus `UserBookmarkMixin` on the User model.
    Existence of the row *is* the bookmark - no state field needed.

        class Post(models.Model):
            bookmarks = bookmarks(user_related_name="post_bookmarks")

        class User(UserBookmarkMixin, AbstractUser):
            pass

        user.bookmark(post); user.toggle_bookmark(post); user.is_bookmarked(post)
        post.bookmarks.all()   # real Django manager
        Post.bookmarks.model   # the generated PostBookmark model

    Same configuration knobs as `votes()`: `user_related_name` required (see its docstring for
    why), `target_related_name`/`target_name` default but overridable, `base_model` (or
    `FEEDBACK_BOOKMARKS_BASE_MODEL`), `extra_fields` to merge in other makers. A host can attach
    `bookmarks()` more than once - see `votes()`'s docstring for the `field=` disambiguation rule.

    `model_name` overrides the generated model's class name (default `<Host><Attr>Bookmark`, e.g.
    `Post.bookmarks` -> `PostBookmarksBookmark`). It also names the table and constraints, so settle it
    before the first migration.

    `model=` binds a model you wrote instead - a class or `"app_label.Model"`, checked against what
    this reads of it once registered; the options that describe a built model are refused beside it.
    See docs/django/apps/feedback/your_own_models.md.
    """
    return _BookmarksField(
        model=model,
        target_name=target_name,
        generating={
            "user_related_name": user_related_name,
            "target_related_name": target_related_name,
            "user_model": user_model,
            "base_model": base_model,
            "extra_fields": extra_fields,
            "model_name": model_name,
        },
    )


class UserBookmarkMixin:
    """Mix into your User model to bookmark anything with `bookmarks()` attached - see `bookmarks`."""

    def bookmark(self, obj, *, field=None):
        field = resolve_field(obj, field, _BookmarksField, "bookmarkable")
        lookup = {field.config.target_name: obj, "user": self}
        field.model.objects.get_or_create(**lookup)

    def unbookmark(self, obj, *, field=None):
        field = resolve_field(obj, field, _BookmarksField, "bookmarkable")
        lookup = {field.config.target_name: obj, "user": self}
        field.model.objects.filter(**lookup).delete()

    def toggle_bookmark(self, obj, *, field=None):
        field = resolve_field(obj, field, _BookmarksField, "bookmarkable")
        if self.is_bookmarked(obj, field=field):
            self.unbookmark(obj, field=field)
        else:
            self.bookmark(obj, field=field)

    def is_bookmarked(self, obj, *, field=None):
        field = resolve_field(obj, field, _BookmarksField, "bookmarkable")
        lookup = {field.config.target_name: obj, "user": self}
        return field.model.objects.filter(**lookup).exists()
