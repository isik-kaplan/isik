# scopes

`isik.django.apps.common.db.scopes` keeps rows inside a scope - a campaign, a workspace, a project - with a
foreign key the database enforces. A row and every row it keys to belong to the same scope, whatever writes
them: the ORM, raw SQL, a migration, a script.

```python
from isik.django.apps.common.db import ScopedForeignKey, ScopedModel

class CampaignScoped(ScopedModel):
    scope_field = "campaign"
    campaign = models.ForeignKey("campaigns.Campaign", on_delete=models.PROTECT, related_name="+")

    class Meta:
        abstract = True

class Attempt(BaseModel, CampaignScoped): ...

class Response(BaseModel, CampaignScoped):
    attempt = ScopedForeignKey(
        "contact_history.Attempt",
        on_delete=models.PROTECT,
        related_name="responses",
        deferrable=models.Deferrable.DEFERRED,
    )
```

## ScopedModel

The base of every model whose rows belong to a scope. `scope_field` names the field holding the scope, which
the subclass declares - a key to the scope's model, or any column. Each concrete subclass is unique on
`(scope, id)` (`<table>_in_scope`), the columns every key to it references.

## ScopedForeignKey

A key to another model scoped by the same field: `(scope, <name>_id)` referencing the target's `(scope, id)`,
one foreign key over both columns. It takes a `ForeignKey`'s arguments, and:

- `deferrable` - a `models.Deferrable`, when Postgres checks the key: at each statement unless set, at commit
  with `Deferrable.DEFERRED` (a transaction moving rows between parents).
- `scope` - the scope field's name; the model's `scope_field` unless given.

On each model it's on, it declares:

- `<name>_id`, a `UUIDField` - its column, holding the target's id. Its `verbose_name` is `"<name> id"`, and
  its `help_text` and `db_comment` are the key's.
- the foreign key, `<table>_<name>_in_scope` - `FOREIGN KEY (scope, <name>_id) REFERENCES target (scope, id)`,
  added once every table of its migration exists, as Django adds its own.
- an index on `(scope, <name>_id)`, for `attempt.responses` and for the check a delete of the target makes.

The ORM treats it as a relation: `response.attempt`, `attempt.responses`, `select_related("attempt")`,
`filter(attempt=...)`, `filter(responses__answer=...)`. Assigning `response.attempt = attempt` sets
`response.attempt_id` and `response.campaign` from the attempt, so a row follows its target into its scope.
`on_delete` is `CASCADE`, `PROTECT`, `RESTRICT`, `SET_NULL` (with `null=True`, nulling `<name>_id`) or
`DO_NOTHING`.

`full_clean()` validates the key with the model's constraints: a target in another scope, or one that isn't
there, is a `ValidationError` naming the key. Forms and the admin show `<name>_id` read-only, and leave the key
to the code.

Every model isik keys has a UUID id, and so does every target of a `ScopedForeignKey` - `isik.E025` holds it.

## On the wire

`ScopedRelatedField` (`isik.django.drf.serializers`) is the key in a serializer, the target's id on the wire as a
plain `PrimaryKeyRelatedField` has it - a UUID string both ways, described so in the OpenAPI schema, `nullable`
where the key is. It's read from `<name>_id`, with no query, and written as an id looked up in `queryset`, within
the scopes the request may write to - `scopes(request)`, a scope or several, a queryset of them included:

- within the scope the request writes, where the serializer has a writable scope field and the request sends it;
- within the instance's scope, on an update;
- within `scopes(request)` alone, on a create whose scope the server derives - the row takes its scope from the
  target it names. Such a write needs `scopes`, or any target of any scope would do: without it, the write is
  refused as configured wrong (`ImproperlyConfigured`).

`scopes` is asked with the serializer's `context["request"]` - a serializer used with no request in its context
(a command, a task) is refused as configured wrong rather than looking up unlimited.

An id outside the lookup reads as one that isn't there - `does_not_exist`, on the field - so a request can't
tell a row in another scope from a row that doesn't exist.

```python
class ResponseSerializer(BaseModelSerializer):
    attempt = ScopedRelatedField(
        scope="campaign",
        queryset=Attempt.objects.all(),
        scopes=lambda request: Campaign.objects.filter(members=request.user),
    )

    class Meta:
        model = Response
        fields = ["id", "campaign", "attempt", "answer"]
        read_only_fields = ["campaign"]   # derived: the response takes its attempt's campaign
```

A project whose rows each belong to a user writes `scopes=lambda request: request.user.pk`.

## The check

`Check.SCOPED_KEYS` (`isik.E025`) holds every scoped model of the project's apps (`CheckOption.APPS`):

- a `ScopedModel` names its `scope_field`, a field it has;
- a key between two models scoped by the same field is a `ScopedForeignKey` - a plain `ForeignKey` or
  `OneToOneField` there is an error, the next one added included;
- a `ScopedForeignKey`'s target is scoped by its field, and keyed by a UUID.

A key from a scoped model to a model with no scope - a lookup table, the user - is a plain `ForeignKey`.
