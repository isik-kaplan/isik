"""notes() maker + UserNoteMixin - see `notes` for usage."""

from django.db import models
from django.db.models import Value

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy, lazy_format
from isik.django.apps.common._contracts import kind
from isik.django.apps.common._model_makers import resolve_field
from isik.django.apps.feedback._attachment import FeedbackAttachment


class _NotesField(FeedbackAttachment):
    """Descriptor returned by `notes()` - see `notes` for the public docstring."""

    maker = "notes()"
    noun = "Note"
    base_setting = "FEEDBACK_NOTES_BASE_MODEL"
    default_related_name = "notes"
    timestamps = ("created_at", "updated_at")

    def __init__(self, *, body_max_length, **keywords):
        super().__init__(**keywords)
        self.body_max_length = body_max_length

    def descriptions(self, host_cls):
        return (
            (
                lazy_format("The %(target)s this note is on.", target=host_cls.__name__),
                f"The {host_cls.__name__} this note is on.",
            ),
            (gettext_lazy("Who wrote this note."), "Who wrote this note."),
        )

    def own_fields(self):
        # CharField, not TextField(max_length=...) - a TextField's max_length is a bare attribute
        # Django never enforces (no auto-validator, no DB-level constraint); CharField gets both:
        # an automatic MaxLengthValidator (full_clean()) and a real varchar(N) column at the DB
        # level. Unbounded (body_max_length=None) still gets the original unbounded TextField.
        described = {"help_text": gettext_lazy("The note's text."), "db_comment": "The note's text."}
        if self.body_max_length:
            return {"body": models.CharField(max_length=self.body_max_length, **described)}
        return {"body": models.TextField(**described)}

    def own_needs(self):
        return (kind("body", (models.TextField, models.CharField), _("a TextField or CharField")),)

    def own_annotations(self, signed_in, user):
        # Notes are private - only the caller's own are counted.
        return (("mine_count", self.count(self.rows().filter(user=user)) if signed_in else Value(0)),)


def notes(
    *,
    model=None,
    user_related_name=None,
    target_name=None,
    target_related_name=None,
    user_model=None,
    base_model=None,
    extra_fields=None,
    model_name=None,
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

    `model_name` overrides the generated model's class name (default `<Host><Attr>Note`, e.g.
    `Post.notes` -> `PostNotesNote`). It also names the table and constraints, so settle it
    before the first migration.

    `model=` binds a model you wrote instead - a class or `"app_label.Model"`, checked against what
    this reads of it once registered; the options that describe a built model are refused beside it.
    See docs/django/apps/feedback/your_own_models.md.
    """
    return _NotesField(
        model=model,
        target_name=target_name,
        body_max_length=body_max_length,
        generating={
            "user_related_name": user_related_name,
            "target_related_name": target_related_name,
            "user_model": user_model,
            "base_model": base_model,
            "extra_fields": extra_fields,
            "model_name": model_name,
            "body_max_length": body_max_length,
        },
    )


class UserNoteMixin:
    """Mix into your User model to write notes on anything with `notes()` attached - see `notes`."""

    def add_note(self, obj, body, *, field=None):
        field = resolve_field(obj, field, _NotesField, "noteable")
        return field.model.objects.create(**{field.config.target_name: obj, "user": self, "body": body})

    def notes_on(self, obj, *, field=None):
        field = resolve_field(obj, field, _NotesField, "noteable")
        return field.model.objects.filter(**{field.config.target_name: obj, "user": self})
