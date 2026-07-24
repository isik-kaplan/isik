"""votes() maker + UserVoteMixin - see `votes` for usage."""

from django.conf import settings
from django.db import models

from isik.django.apps.common._model_makers import (
    build_model,
    claim_related_name,
    expose_via_reverse_accessor,
    resolve_base_model,
    resolve_field,
)


class _VotesField:
    """Descriptor returned by `votes()` - see `votes` for the public docstring."""

    def __init__(
        self,
        *,
        user_related_name,
        target_name="target",
        target_related_name="votes",
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
        model_name = f"{host_cls.__name__}{name.capitalize()}Vote"
        claim_related_name(host_cls, self.target_related_name, model_name)
        claim_related_name(self.user_model, self.user_related_name, model_name)

        fields = {
            self.target_name: models.ForeignKey(
                host_cls, on_delete=models.CASCADE, related_name=self.target_related_name
            ),
            "user": models.ForeignKey(self.user_model, on_delete=models.CASCADE, related_name=self.user_related_name),
            "value": models.SmallIntegerField(choices=[(1, "up"), (-1, "down")]),
            **self.extra_fields,
        }
        generated_model = build_model(
            model_name,
            host_cls,
            fields=fields,
            base_model=resolve_base_model(self.base_model, "FEEDBACK_VOTES_BASE_MODEL"),
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


def votes(
    *,
    user_related_name,
    target_name="target",
    target_related_name="votes",
    user_model=None,
    base_model=None,
    extra_fields=None,
):
    """
    Attaches a per-host-model up/downvote through-table, plus `UserVoteMixin` on the User model
    to cast/retract votes.

        class Post(models.Model):
            votes = votes(user_related_name="post_votes")

        class User(UserVoteMixin, AbstractUser):
            pass

        user.upvote(post); user.downvote(post); user.unvote(post)
        post.votes.all()       # real Django manager - reverse FK to PostVote
        Post.votes.model       # the generated PostVote model

    `user_related_name` is required, not guessed from the host's name - two voteable models
    sharing a guessed name would silently clash on the User model (see
    `common._model_makers.claim_related_name`). `target_related_name` (default "votes") and
    `target_name` (default "target", the FK field pointing at the host) are also configurable in
    case either collides with something. `base_model` (or the process-wide
    `FEEDBACK_VOTES_BASE_MODEL` setting) lets the generated `<Host>Vote` model inherit your own
    base instead of the built-in timestamp-only default. `extra_fields` merges in other makers by
    attribute name, e.g. `votes(extra_fields={"bookmarks": bookmarks(...)})`.

    A host can attach `votes()` more than once (e.g. `Post.upvotes = votes(...)` and
    `Post.helpfulness = votes(...)`) - `UserVoteMixin` methods auto-pick the sole attachment when
    there's only one, and require `field=Post.upvotes` to disambiguate when there's more than one.
    """
    return _VotesField(
        user_related_name=user_related_name,
        target_name=target_name,
        target_related_name=target_related_name,
        user_model=user_model,
        base_model=base_model,
        extra_fields=extra_fields,
    )


class UserVoteMixin:
    """Mix into your User model to cast votes on anything with `votes()` attached - see `votes`."""

    def upvote(self, obj, *, field=None):
        self._set_vote(obj, 1, field)

    def downvote(self, obj, *, field=None):
        self._set_vote(obj, -1, field)

    def unvote(self, obj, *, field=None):
        field = resolve_field(obj, field, _VotesField, "voteable")
        field.model.objects.filter(**{field.config.target_name: obj, "user": self}).delete()

    def _set_vote(self, obj, value, field):
        field = resolve_field(obj, field, _VotesField, "voteable")
        lookup = {field.config.target_name: obj, "user": self}
        field.model.objects.update_or_create(**lookup, defaults={"value": value})
