"""What the sync tests share: a world set up as a protocol fixture's `given` says, and its `act`s."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction

from isik.django.apps.sync.log import prune_sync_log
from isik.django.conf import override_isik
from tests.testapp.models import SyncNote as Note
from tests.testapp.models import SyncProject as Project
from tests.testapp.models import SyncTask as Task
from tests.testapp.models import SyncTeam as Team
from tests.testapp.models import SyncTeamMembership as TeamMembership


MODELS = {"tasks": Task, "projects": Project, "notes": Note}
# Created in this order, so a row's key finds its target.
ORDER = ("projects", "tasks", "notes")
# {model: {its key's field: the model the key points at}}
KEYS = {"tasks": {"owner": "user", "project": None}, "projects": {"owner": "user"}, "notes": {"team": "team"}}


def user(pk):
    """The user `pk` - made the first time it's named."""
    found, _ = get_user_model().objects.get_or_create(pk=pk, defaults={"email": f"{pk}@example.com", "username": pk})
    return found


def team(pk):
    """The team `pk` - made the first time it's named."""
    found, _ = Team.objects.get_or_create(pk=pk)
    return found


def _values(name, row):
    values = {}
    for field, value in row.items():
        target = KEYS[name].get(field, "plain") if field in KEYS[name] else "plain"
        if target == "user" and value is not None:
            user(value)
        if target == "team" and value is not None:
            team(value)
        values[f"{field}_id" if field in KEYS[name] else field] = value
    return values


def create(name, row):
    return MODELS[name].objects.create(**_values(name, row))


def create_many(name, rows):
    """Creates `rows` in one transaction."""
    with transaction.atomic():
        return [create(name, row) for row in rows]


def update(name, pk, changes):
    row = MODELS[name].objects.get(pk=pk)
    for field, value in _values(name, changes).items():
        setattr(row, field, value)
    row.save()


def delete(name, pk):
    MODELS[name].objects.filter(pk=pk).delete()


def grant(member, scope):
    TeamMembership.objects.create(user=user(member), team=team(scope.split(":", 1)[1]))


def revoke(member, scope):
    TeamMembership.objects.filter(user_id=member, team_id=scope.split(":", 1)[1]).delete()


def expire():
    """Prunes every change in the log - so every cursor issued so far is past retention."""
    with override_isik(DJANGO={"SYNC": {"RETENTION": timedelta(microseconds=1)}}):
        prune_sync_log()


def given(world):
    """Sets up a fixture's `given`: its user, their memberships and its rows. Returns the user, or None."""
    member = None if world["user"] is None else user(world["user"])
    for scope in world["memberships"]:
        grant(world["user"], scope)
    for name in ORDER:
        for row in world["rows"].get(name, ()):
            create(name, row)
    return member


def act(member, step):
    """Changes the world as a fixture's `act` step says - as another actor would."""
    ((what, how),) = step.items()
    if what == "create":
        create(how["model"], how["row"])
    elif what == "update":
        update(how["model"], how["id"], how["set"])
    elif what == "delete":
        delete(how["model"], how["id"])
    elif what == "grant":
        grant(member.pk, how["scope"])
    elif what == "revoke":
        revoke(member.pk, how["scope"])
    else:
        expire()
