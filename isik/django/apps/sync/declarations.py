"""
Declaring what syncs - `syncs()` on a model, and `Memberships`, how people join a scope:

    @syncs("tasks", scope="owner")                       # each person's own rows: the scope kind "user"
    class Task(BaseModel): ...

    @syncs("notes", scope="team", memberships=Memberships("teams.Membership", user="user", scope="team"))
    class Note(BaseModel): ...

A scope is a string, `"<kind>:<id>"` - `"user:0190…"`, `"team:0190…"`: the row's `scope` field's value, under
its kind. A person sees the rows of every scope they're in: their own `user:<their pk>` always, and each scope
a membership row puts them in. Each change to a synced row writes a sync log entry, and each membership row
written or deleted grants or revokes its scope - by triggers, in the transaction making the change.
"""

import re
from dataclasses import dataclass

import pgtrigger
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Q
from django.db.models.fields.related import resolve_relation
from django.db.models.utils import make_model_tuple

from isik._internal.translation import gettext as _


# A kind - and a feed name - is a plain identifier: it goes into SQL, and into every scope string.
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
# The kind of the scope every person is in: their own.
USER_KIND = "user"

# {name: Synced} - every model syncs() declared, by the name the feed and push call it.
SYNCED = {}
# The (membership model label, kind) pairs a trigger is registered for already.
_MEMBERSHIP_TRIGGERS = set()


@dataclass(frozen=True)
class Memberships:
    """
    How people join a scope: one row of `model` - a model, or its `"app_label.Model"` - per person (`user`, a key
    to the user model) in a scope (`scope`, a field holding the scope's id). A row written grants the person the
    scope; a row deleted, or moved to another person or scope, revokes it.

    With a `condition` - a `Q` over the membership model's own fields, `Q(active=True)` - only a row meeting it
    puts its person in the scope: a row starting to meet it grants the scope, and one ceasing to revokes it.
    """

    model: object
    user: str
    scope: str
    condition: Q | None = None

    @property
    def label(self):
        """The membership model's `"app_label.Model"`."""
        return self.model if isinstance(self.model, str) else self.model._meta.label

    def resolve(self):
        """The membership model."""
        from django.apps import apps

        return apps.get_model(self.label)

    def attname(self, name):
        """The column attribute of the membership model's field `name` - `team_id` for a key."""
        return self.resolve()._meta.get_field(name).attname


@dataclass
class Synced:
    """A model `syncs()` declared - see its arguments there."""

    name: str
    model: type
    scope: str
    kind_given: str | None
    memberships: Memberships | None
    viewset: object
    indexed: tuple | None

    @property
    def scope_field(self):
        """The model's field holding the scope's id."""
        return self.model._meta.get_field(self.scope)

    @property
    def kind(self):
        """
        The scope's kind: the one given, else `"user"` for a key to the user model, else the model the key
        points at's name - None for a field that isn't a key and was given none.
        """
        if self.kind_given is not None:
            return self.kind_given
        try:
            field = self.scope_field
        except FieldDoesNotExist:
            return None
        if not field.is_relation:
            return None
        # Read off the key, not related_model, which waits for every model to load - a membership trigger asks
        # as soon as the two models it needs are.
        target = field.remote_field.model
        if target._meta.label == settings.AUTH_USER_MODEL:
            return USER_KIND
        return target._meta.model_name

    def scope_of(self, value):
        """The scope string a row holding `value` in its scope field is in - None for none."""
        return None if value is None else f"{self.kind}:{value}"


def syncs(name=None, *, scope, kind=None, memberships=None, viewset=None, indexed=None):
    """
    Declares the decorated model synced, under `name` (its model name unless given): every change to a row
    writes a sync log entry, the feed serves its rows, and push writes them.

    - `scope` - the field holding the id of the scope the row is in: a key to the user model for a person's own
      rows (kind `"user"`), a key to a team, an organization - whatever a project scopes by.
    - `kind` - the scope's kind, when it isn't the key's: `"user"` for a key to the user model, else the model
      the key points at's name. A field that isn't a key needs one.
    - `memberships` - a `Memberships`, how people join a scope of this kind. Every kind but `"user"` needs it,
      declared on one of the models scoped by it at least.
    - `viewset` - the viewset, or its dotted path, serving the model's REST API: what the feed reads a row with
      and push writes it through. Unset, the one `ViewSetRegistryMixin` registered for the model.
    - `indexed` - the column lists a client indexes its copy by, `[["owner"], ["done", "title"]]`. Unset, each
      key and each index of the model's own fields.

    The row's REST representation must be flat - keys as ids, nothing nested - and carry `scope`; the model needs
    a concurrency token, covering no relations; `isik.E027` refuses what can't work.
    """

    def declare(model):
        from isik.django.apps.sync.triggers import SyncLogTrigger

        synced = Synced(
            name=name or model._meta.model_name,
            model=model,
            scope=scope,
            kind_given=kind,
            memberships=memberships,
            viewset=viewset,
            indexed=None if indexed is None else tuple(tuple(columns) for columns in indexed),
        )
        if synced.name in SYNCED and SYNCED[synced.name].model._meta.label != model._meta.label:
            raise ValueError(
                _("%(name)s is synced already, by %(model)s.")
                % {"name": synced.name, "model": SYNCED[synced.name].model._meta.label}
            )
        SYNCED[synced.name] = synced
        model.synced = synced
        pgtrigger.register(SyncLogTrigger(synced=synced))(model)
        if memberships is not None:
            _watch_memberships(synced)
        return model

    return declare


def _watch_memberships(synced):
    from isik.django.apps.sync.triggers import MembershipTrigger

    def register(membership_model, *targets):
        # The kind read now, once the membership model and the model the scope field's key points at are both
        # loaded - either may load after the synced one.
        key = (membership_model._meta.label, synced.kind)
        if key in _MEMBERSHIP_TRIGGERS:
            return
        _MEMBERSHIP_TRIGGERS.add(key)
        pgtrigger.register(MembershipTrigger(kind=synced.kind, memberships=synced.memberships))(membership_model)

    waits = [make_model_tuple(synced.memberships.label)]
    remote = None if synced.kind_given is not None else _remote_model(synced)
    if remote is not None:
        waits.append(make_model_tuple(resolve_relation(synced.model, remote)))
    synced.model._meta.apps.lazy_model_operation(register, *waits)


def _remote_model(synced):
    # What the scope field's key points at, as declared - None for a field that isn't there, or isn't a key.
    try:
        field = synced.scope_field
    except FieldDoesNotExist:
        return None
    return field.remote_field.model if field.is_relation else None


# [(synced model name, relation name)] - each covered relation whose rows log their synced row, see
# log_relation_parents().
PARENT_LOGS = []


def log_relation_parents():
    """
    Gives each relation a synced model's version covers (`concurrency_relations`) a `ParentLogTrigger` on the
    table its rows live in - on a proxy in the synced model's app, as isik's relation locks are - so a change to
    one is a change of the synced row the feed hears of. Run once every model is loaded, when the sync app is
    ready: the models pointing at a synced one may load after it.
    """
    from isik.common.utils.strings import snake_to_pascal
    from isik.django.apps.common.db.concurrency import _relation, _relation_table, concurrency_relations
    from isik.django.apps.common.generated import mark_generated
    from isik.django.apps.sync.triggers import ParentLogTrigger

    for synced in synced_models().values():
        for name in concurrency_relations(synced.model):
            relation = _relation(synced.model, name)
            if relation is None or (synced.name, name) in PARENT_LOGS:
                continue
            table, owner_key = _relation_table(relation)
            trigger = ParentLogTrigger(synced=synced, relation=name, owner_key=owner_key)
            meta = type("Meta", (), {"proxy": True, "app_label": synced.model._meta.app_label})
            proxy_name = f"{synced.model.__name__}{snake_to_pascal(name)}SyncLog"
            attributes = {"__module__": synced.model.__module__, "Meta": meta}
            # Registered here, not left to pgtrigger's reading of each model's Meta, which may have run already.
            pgtrigger.register(trigger)(mark_generated(type(proxy_name, (table,), attributes), "lock"))
            PARENT_LOGS.append((synced.name, name))


def synced_models():
    """Every model `syncs()` declared, by name, in name order."""
    return dict(sorted(SYNCED.items()))


def memberships_by_kind():
    """{kind: Memberships} - each scope kind's memberships, from the first synced model declaring them."""
    found = {}
    for synced in synced_models().values():
        if synced.memberships is not None:
            found.setdefault(synced.kind, synced.memberships)
    return found


def scopes_of(user):
    """Every scope `user` is in: their own, and each one a membership row puts them in."""
    found = [f"{USER_KIND}:{user.pk}"]
    for kind, memberships in memberships_by_kind().items():
        scope = memberships.attname(memberships.scope)
        rows = memberships.resolve()._default_manager.filter(
            memberships.condition or Q(), **{memberships.attname(memberships.user): user.pk, f"{scope}__isnull": False}
        )
        found.extend(f"{kind}:{value}" for value in rows.values_list(scope, flat=True))
    return found
