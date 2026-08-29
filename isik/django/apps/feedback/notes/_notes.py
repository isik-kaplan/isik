"""notes() maker + UserNoteMixin - see `notes` for usage."""

from django.conf import settings
from django.db import models

from isik.django.apps.common._model_makers import (
    build_model,
    claim_related_name,
    expose_via_reverse_accessor,
    resolve_base_model,
    resolve_field,
)


class _NotesField:
    """Descriptor returned by `notes()` - see `notes` for the public docstring."""

    def __init__(
        self,
        *,
        user_related_name,
        target_name,
        target_related_name,
        user_model=None,
        base_model=None,
        extra_fields=None,
        body_max_length=None,
    ):
        self.user_related_name = user_related_name
        self.target_name = target_name
        self.target_related_name = target_related_name
        self.user_model = user_model or settings.AUTH_USER_MODEL
        self.base_model = base_model
        self.extra_fields = extra_fields or {}
        self.body_max_length = body_max_length

    def contribute_to_class(self, host_cls, name):
        model_name = f"{host_cls.__name__}{name.capitalize()}Note"
        claim_related_name(host_cls, self.target_related_name, model_name)
        claim_related_name(self.user_model, self.user_related_name, model_name)

        # CharField, not TextField(max_length=...) - a TextField's max_length is a bare attribute
        # Django never enforces (no auto-validator, no DB-level constraint); CharField gets both:
        # an automatic MaxLengthValidator (full_clean()) and a real varchar(N) column at the DB
        # level. Unbounded (body_max_length=None) still gets the original unbounded TextField.
        body_field = models.CharField(max_length=self.body_max_length) if self.body_max_length else models.TextField()
        fields = {
            self.target_name: models.ForeignKey(
                host_cls, on_delete=models.CASCADE, related_name=self.target_related_name
            ),
            "user": models.ForeignKey(self.user_model, on_delete=models.CASCADE, related_name=self.user_related_name),
            "body": body_field,
            **self.extra_fields,
        }
        generated_model = build_model(
            model_name,
            host_cls,
            fields=fields,
            base_model=resolve_base_model(self.base_model, "FEEDBACK_NOTES_BASE_MODEL"),
        )
        expose_via_reverse_accessor(
            host_cls, name, self.target_related_name, generated_model=generated_model, config=self
        )


def notes(
    *,
    user_related_name,
    target_name="target",
    target_related_name="notes",
    user_model=None,
    base_model=None,
    extra_fields=None,
    body_max_length=None,
):
    """
    Attaches a per-host-model note log, plus `UserNoteMixin` on the User model. Unlike
    `bookmarks()`, this is a log, not a toggle - multiple notes per user per object, each
    independently editable/deletable (e.g. through a `ModelViewSet` scoped to the requesting
    user's own rows), no unique constraint.

        class Post(models.Model):
            notes = notes(user_related_name="post_notes")

        class User(UserNoteMixin, AbstractUser):
            pass

        note = user.add_note(post, "remember to follow up")
        user.notes_on(post)  # this user's notes on `post`
        Post.notes.model     # the generated PostNote model

    Same configuration knobs as `votes()`/`bookmarks()`, plus `body_max_length` (default
    unbounded). A host can attach `notes()` more than once - see `votes()`'s docstring for the
    `field=` disambiguation rule.
    """
    return _NotesField(
        user_related_name=user_related_name,
        target_name=target_name,
        target_related_name=target_related_name,
        user_model=user_model,
        base_model=base_model,
        extra_fields=extra_fields,
        body_max_length=body_max_length,
    )


class UserNoteMixin:
    """Mix into your User model to write notes on anything with `notes()` attached - see `notes`."""

    def add_note(self, obj, body, *, field=None):
        field = resolve_field(obj, field, _NotesField, "noteable")
        return field.model.objects.create(**{field.config.target_name: obj, "user": self, "body": body})

    def notes_on(self, obj, *, field=None):
        field = resolve_field(obj, field, _NotesField, "noteable")
        return field.model.objects.filter(**{field.config.target_name: obj, "user": self})
