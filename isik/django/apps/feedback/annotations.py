"""with_feedback() and ranked() - see each one's own docstring."""

from django.db.models import Case, F, FloatField, Value, When
from django.db.models.functions import Cast, Greatest, Least, Power, Sqrt
from django.db.models.lookups import Exact

from isik._internal.translation import gettext as _
from isik.django.apps.common._model_makers import attached
from isik.django.apps.feedback._attachment import FeedbackAttachment
from isik.django.apps.feedback.votes._votes import _VotesField


# The z of a 95% confidence interval - what the Wilson lower bound is taken at.
WILSON_Z = 1.96

RANKINGS = ("score", "wilson", "controversial")


def with_feedback(queryset, user=None, *, only=None):
    """
    `queryset` - of a host - with what each of its `votes()`, `comments()`, `notes()` and `bookmarks()`
    holds, one subquery each, so a list costs one query however many rows it has. Each is named
    `<attachment>_<what>`:

        votes       <name>_score, <name>_up, <name>_down, <name>_mine (the user's 1/-1, or None)
        comments    <name>_count
        bookmarks   <name>_count, <name>_mine (whether the user bookmarked it)
        notes       <name>_mine_count (notes are private - only the user's own)

        posts = with_feedback(Post.objects.all(), request.user)
        posts[0].votes_score, posts[0].bookmarks_mine

    `user` is who's asking - None, or an anonymous user, has no `_mine`s: None, False and 0.
    `only` limits it to the attachments named.
    """
    found = {
        descriptor.config.attname: descriptor.config for descriptor in attached(queryset.model, FeedbackAttachment)
    }
    if only is not None:
        unknown = sorted(set(only) - set(found))
        if unknown:
            raise ValueError(
                _("%(model)s has no votes(), comments(), notes() or bookmarks() named %(names)s")
                % {"model": queryset.model.__name__, "names": ", ".join(unknown)}
            )
        found = {name: config for name, config in found.items() if name in only}
    for config in found.values():
        queryset = queryset.annotate(**config.annotations(user))
    return queryset


def summary(config, row, user=None):
    """
    `{what: value}` - what attachment `config` holds of the host row `row`, as `with_feedback()` names
    it without the `<attachment>_` prefix: read off `row` if its queryset was annotated, else queried
    for it alone, as `user`.
    """
    names = list(config.annotations(None))
    if all(hasattr(row, name) for name in names):
        values = {name: getattr(row, name) for name in names}
    else:
        rows = type(row)._default_manager.filter(pk=row.pk)
        values = with_feedback(rows, user, only=[config.attname]).values(*names).get()
    return {name.removeprefix(f"{config.attname}_"): value for name, value in values.items()}


def ranked(queryset, field, *, by="score", user=None):
    """
    `queryset` - of a host - ordered best first by its `votes()` attachment `field` (e.g. `Post.votes`),
    with the rank as `<attachment>_rank` beside `with_feedback()`'s own:

        score           upvotes minus downvotes
        wilson          the lower bound of the 95% Wilson interval on the share of upvotes - a few votes
                        all up rank below many mostly up, so new rows can't jump the list on one vote
        controversial   many votes, split evenly - (up + down) ^ (fewer / more), 0 with either side empty

    Ties go to the higher primary key, so the order is stable. Nothing is stored - each is computed in
    the query.
    """
    config = field.config
    if not isinstance(config, _VotesField):
        raise TypeError(_("ranked() ranks by a votes() attachment, not %(field)r") % {"field": config})
    if by not in RANKINGS:
        raise ValueError(_("ranked() ranks by %(rankings)s, not %(by)r") % {"rankings": ", ".join(RANKINGS), "by": by})
    name = config.attname
    queryset = with_feedback(queryset, user, only=[name])
    up = Cast(F(f"{name}_up"), FloatField())
    down = Cast(F(f"{name}_down"), FloatField())
    rank = f"{name}_rank"
    queryset = queryset.annotate(**{rank: _ranking(by, F(f"{name}_score"), up, down)})
    return queryset.order_by(F(rank).desc(), "-pk")


def _ranking(by, score, up, down):
    if by == "score":
        return Cast(score, FloatField())
    if by == "wilson":
        votes = up + down
        z = Value(WILSON_Z)
        share = up / votes
        spread = z * Sqrt((share * (Value(1.0) - share) + z * z / (Value(4.0) * votes)) / votes)
        return _unless_zero(votes, (share + z * z / (Value(2.0) * votes) - spread) / (Value(1.0) + z * z / votes))
    return _unless_zero(Least(up, down), Power(up + down, Least(up, down) / Greatest(up, down)))


def _unless_zero(divisor, expression):
    """`expression`, or 0 where `divisor` - what it divides by - is 0."""
    return Case(When(Exact(divisor, Value(0.0)), then=Value(0.0)), default=expression)
