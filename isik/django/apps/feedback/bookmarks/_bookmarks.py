"""bookmarks() maker + UserBookmarkMixin - see `bookmarks` for usage."""

from django.conf import settings
from django.db import models

from isik.django.apps.common._model_makers import (
    build_model,
    claim_related_name,
    expose_via_reverse_accessor,
    resolve_base_model,
    resolve_field,
)


class _BookmarksField:
    """Descriptor returned by `bookmarks()` - see `bookmarks` for the public docstring."""

    def __init__(
        self,
        *,
        user_related_name,
        target_name="target",
        target_related_name="bookmarks",
        user_model=None,
        base_model=None,
        extra_fields=None,
    ):
        self.user_related_name = user_related_name
        self.target_name = target_name
        self.target_related_name = target_related_name
        self.user_model = user_model or settings.AUTH_USER_MODEL
        self.base_model = base_model
        self.extra_fields = extra_fields or {}

    def contribute_to_class(self, host_cls, name):
        model_name = f"{host_cls.__name__}{name.capitalize()}Bookmark"
        claim_related_name(host_cls, self.target_related_name, model_name)
        claim_related_name(self.user_model, self.user_related_name, model_name)

        fields = {
            self.target_name: models.ForeignKey(
                host_cls, on_delete=models.CASCADE, related_name=self.target_related_name
            ),
            "user": models.ForeignKey(self.user_model, on_delete=models.CASCADE, related_name=self.user_related_name),
            **self.extra_fields,
        }
        generated_model = build_model(
            model_name,
            host_cls,
            fields=fields,
            base_model=resolve_base_model(self.base_model, "FEEDBACK_BOOKMARKS_BASE_MODEL"),
            meta_attrs={
                "constraints": [
                    models.UniqueConstraint(
                        fields=[self.target_name, "user"], name=f"unique_{model_name.lower()}_per_user"
                    )
                ]
            },
        )
        expose_via_reverse_accessor(
            host_cls, name, self.target_related_name, generated_model=generated_model, config=self
        )


def bookmarks(
    *,
    user_related_name,
    target_name="target",
    target_related_name="bookmarks",
    user_model=None,
    base_model=None,
    extra_fields=None,
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
    """
    return _BookmarksField(
        user_related_name=user_related_name,
        target_name=target_name,
        target_related_name=target_related_name,
        user_model=user_model,
        base_model=base_model,
        extra_fields=extra_fields,
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
