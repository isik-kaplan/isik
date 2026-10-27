# Viewset actions

`isik.django.apps.feedback.viewsets` - mixins adding a host's feedback to its own viewset:

```python
from isik.django.apps.feedback.viewsets import BookmarkActions, CommentActions, NoteActions, VoteActions

class PostViewSet(VoteActions, BookmarkActions, CommentActions, NoteActions, BaseModelViewSet):
    model = Post
    endpoint = "posts"
    serializer_class = PostSerializer
```

| mixin | routes |
|---|---|
| `VoteActions` | `PUT /posts/{id}/vote/` with `{"value": 1}` or `-1`, `DELETE` to take it back - both answer `{score, up, down, mine}` |
| `BookmarkActions` | `PUT` / `DELETE /posts/{id}/bookmark/` - both answer `{count, mine}` |
| `CommentActions` | `GET` (a page, oldest first) / `POST /posts/{id}/comments/`; `PATCH` / `DELETE /posts/{id}/comments/{comment_pk}/` |
| `NoteActions` | `GET` / `POST /posts/{id}/notes/`; `PATCH` / `DELETE /posts/{id}/notes/{note_pk}/` - the caller's own only |

## Who can do what

- Every action fetches the host with the view's own `get_object()`, so a row the view's queryset
  doesn't hold is a 404 - the host queryset decides what can be voted on, bookmarked, commented on or
  noted.
- The actions are checked with `feedback_permission_classes` (default `IsAuthenticatedOrReadOnly`)
  instead of the view's `permission_classes`, which are about writing the host - voting on a post isn't
  editing it. Put the host's read permissions there too if its queryset doesn't already scope it.
- Writes need a signed-in caller. A comment is edited or deleted by its author, or by whoever
  `can_moderate_comment(request, comment)` allows (nobody, unless you override it). Notes are private:
  every notes route needs a signed-in caller and sees only their own.

## Comments and threads

On a threaded attachment each comment carries `parent` and `reply_count`. `POST` with a `parent` is a
reply - refused if the parent is on another host or the reply would go past `max_depth`.
`?parent=<comment>` lists one comment's replies, `?top_level=true` the comments replying to none - the
thread comes back flat, for the client to nest. An edit stamps `edited_at` where the model has one,
and can't move a comment under another.

## Picking an attachment

With one attachment of a kind the mixin finds it. With several, name one: `vote_field = "helpfulness"`
(likewise `bookmark_field`, `comment_field`, `note_field`).

## Serializers

Comments and notes are serialized with `comment_serializer_class` / `note_serializer_class` if set,
else the serializer registered for the model, else `generic_comment_serializer()` /
`generic_note_serializer()`'s.

## isik's write guards

On a `BaseModelViewSet` the actions come with isik's own exemptions, merged under the view's - a view's
own entry for an action wins:

- every action is exempt from `If-Match` - none of them writes the host row;
- setting a vote or bookmark, and editing or deleting a comment or note, are exempt from the
  `Idempotency-Key` - a retry changes nothing;
- creating a comment or note takes the key but isn't replayed - its response isn't the host's, which a
  replay by reference re-serializes. A project on the `with_body` claims can take them out of
  `idempotency_no_replay_actions`.

## The schema

With isik's [`AutoSchema`](../../drf/spectacular.md) each route is described: the list routes as
pages, the vote and bookmark routes with their summaries, the creates as 201.
