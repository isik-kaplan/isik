# votes

## votes()
Attaches a per-host up/downvote through-table via `contribute_to_class` - one row per `(target, user)`, enforced by a DB unique constraint. Reach for it when a model needs simple up/down voting with no migration to hand-write.

```python
from isik.django.apps.feedback.votes import UserVoteMixin, votes

class Post(models.Model):
    votes = votes(user_related_name="post_votes")

Post.votes.model                     # generated PostVote model
Post.votes.model.objects.filter(target=post)
```

- `user_related_name` is required, not guessed from the host's name - two voteable models sharing a guessed name would silently clash on the User model.
- A host can attach `votes()` more than once (e.g. `Post.upvotes`/`Post.helpfulness`) - pass `field=Post.upvotes` to disambiguate when there's more than one.

## UserVoteMixin
Mix into your User model to get `upvote`/`downvote`/`unvote` verbs for any host with `votes()` attached.

```python
class User(UserVoteMixin, AbstractUser):
    pass

user.upvote(post)
user.downvote(post)
user.unvote(post)
```
