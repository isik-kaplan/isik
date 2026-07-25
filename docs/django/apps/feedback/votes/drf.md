# drf

`generic_vote_serializer(model)` builds a default `ModelSerializer` for a generated `<Host>Vote` model - `id`, `value`, `created_at`, read-only `user`. Use as-is or subclass further.

```python
from isik.django.apps.feedback.votes.drf import generic_vote_serializer

VoteSerializer = generic_vote_serializer(Post.votes.model)
```

- `user` is never client-writable - the view (e.g. `perform_create`) still has to set it. Pair with `isik.django.drf.permissions.is_owner("user")` for a private vote viewset.
