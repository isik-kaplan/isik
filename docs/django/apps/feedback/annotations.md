# Annotations

What a host's feedback holds, computed in the query - nothing is stored on the host, so nothing can
drift from the rows it counts.

## with_feedback(queryset, user=None, *, only=None)

Annotates a host's queryset with each of its `votes()`, `comments()`, `notes()` and `bookmarks()` - one
correlated subquery each, so a list costs one query however many rows it has, and two kinds of
feedback can't multiply each other's counts as joins would.

```python
from isik.django.apps.feedback.annotations import with_feedback

posts = with_feedback(Post.objects.all(), request.user)
posts[0].votes_score, posts[0].votes_mine, posts[0].bookmarks_mine
```

| attachment | annotations |
|---|---|
| votes | `<name>_score` (up minus down), `<name>_up`, `<name>_down`, `<name>_mine` (the user's 1/-1, or None) |
| comments | `<name>_count` |
| bookmarks | `<name>_count`, `<name>_mine` (whether the user bookmarked it) |
| notes | `<name>_mine_count` - notes are private, so only the user's own |

- `<name>` is the attribute: `Post.helpfulness = votes(...)` gives `helpfulness_score`.
- `user` is who's asking. None, or an anonymous user, has no `_mine`: None, False and 0.
- `only=["votes"]` limits it to the attachments named; a name the host doesn't have is a `ValueError`.

## ranked(queryset, field, *, by="score", user=None)

Orders a host's queryset best first by one `votes()` attachment, with the rank as `<name>_rank`
beside `with_feedback()`'s own:

```python
from isik.django.apps.feedback.annotations import ranked

ranked(Post.objects.all(), Post.votes, by="wilson")
```

- `score` - up minus down.
- `wilson` - the lower bound of the 95% Wilson interval on the share of upvotes. A few votes all up rank
  below many mostly up, so a new row can't jump the list on one vote.
- `controversial` - many votes, split evenly: `(up + down) ^ (fewer / more)`, 0 with either side empty.

Ties go to the higher primary key, so the order is stable.

## Serializer fields

`isik.django.apps.feedback.drf` - read-only, the whole row as their source, reading
`with_feedback()`'s annotations. A row that isn't annotated - a create's response - is read with one
query of its own, as the request's user.

```python
from isik.django.apps.feedback.drf import BookmarksField, CommentCountField, NoteCountField, VotesField

class PostSerializer(BaseModelSerializer):
    votes = VotesField(Post.votes)                 # {"score", "up", "down", "mine"}
    bookmarks = BookmarksField(Post.bookmarks)     # {"count", "mine"}
    comment_count = CommentCountField(Post.comments)
    note_count = NoteCountField(Post.notes)        # the caller's own
```

The [viewset actions](viewsets.md) annotate `list` and `retrieve` for you.
