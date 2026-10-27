"""votes() maker + UserVoteMixin - see `votes` for usage."""

from django.db import models
from django.db.models import Subquery, Sum, Value
from django.db.models.functions import Coalesce

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy, lazy_format
from isik.django.apps.common._contracts import kind
from isik.django.apps.common._model_makers import resolve_field
from isik.django.apps.feedback._attachment import FeedbackAttachment


class _VotesField(FeedbackAttachment):
    """Descriptor returned by `votes()` - see `votes` for the public docstring."""

    maker = "votes()"
    noun = "Vote"
    base_setting = "FEEDBACK_VOTES_BASE_MODEL"
    default_related_name = "votes"
    unique_per_user = True

    def descriptions(self, host_cls):
        return (
            (
                lazy_format("The %(target)s this vote is on.", target=host_cls.__name__),
                f"The {host_cls.__name__} this vote is on.",
            ),
            (gettext_lazy("Who cast this vote."), "Who cast this vote."),
        )

    def own_fields(self):
        return {
            "value": models.SmallIntegerField(
                choices=[(1, "up"), (-1, "down")],
                help_text=gettext_lazy("1 for an upvote, -1 for a downvote."),
                db_comment="1 for an upvote, -1 for a downvote.",
            ),
        }

    def own_needs(self):
        return (kind("value", models.IntegerField, _("an IntegerField holding 1 or -1")),)

    def own_annotations(self, signed_in, user):
        rows = self.rows()
        score = Coalesce(Subquery(rows.values(self.target_name).annotate(n=Sum("value")).values("n")), 0)
        if signed_in:
            # One row at most - a user's vote is unique on its target.
            mine = Subquery(rows.filter(user=user).values("value"))
        else:
            mine = Value(None, output_field=models.SmallIntegerField())
        return (
            ("score", score),
            ("up", self.count(rows.filter(value=1))),
            ("down", self.count(rows.filter(value=-1))),
            ("mine", mine),
        )


def votes(
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

    `model_name` overrides the generated model's class name (default `<Host><Attr>Vote`, e.g.
    `Post.votes` -> `PostVotesVote`). It also names the table and constraints, so settle it before
    the first migration.

    `model=` binds a model you wrote instead - a class or `"app_label.Model"`, checked against what
    this reads of it once registered; the options that describe a built model are refused beside it.
    See docs/django/apps/feedback/your_own_models.md.
    """
    return _VotesField(
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
