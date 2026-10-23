"""PreconditionMixin over a version that covers relations - a membership change is a change a write can lose."""

import pytest
from rest_framework import status

from isik.django.apps.common.db.concurrency import current_concurrency_etag
from tests.testapp.models import EmailUser, Roster, Squad, Tag, Team, TeamNote


pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("urls", "atomic_requests")]


@pytest.fixture
def team():
    return Team.objects.create(name="core")


@pytest.fixture
def bob():
    return EmailUser.objects.create(username="bob", email="bob@example.com")


def etag(team):
    return current_concurrency_etag(Team, team.pk)


def patch(client, path, if_match):
    return client.patch(path, {"name": "platform"}, format="json", headers={"If-Match": if_match})


def test_retrieve_and_a_list_send_the_computed_etag(client, team):
    retrieved = client.get(f"/teams/{team.pk}/")
    listed = client.get("/teams/")

    assert retrieved["ETag"] == retrieved.json()["etag"] == etag(team)
    assert listed.json()[0]["etag"] == etag(team)


def test_a_write_made_before_a_member_joined_is_refused(client, team, bob):
    read = etag(team)
    team.members.add(bob)

    response = patch(client, f"/teams/{team.pk}/", read)

    assert response.status_code == status.HTTP_412_PRECONDITION_FAILED
    assert response.json()["current"]["members"] == [bob.pk]
    assert response["ETag"] == etag(team)


def test_a_write_made_against_the_current_members_lands(client, team, bob):
    team.members.add(bob)

    response = patch(client, f"/teams/{team.pk}/", etag(team))

    assert response.status_code == status.HTTP_200_OK
    assert response["ETag"] == response.json()["etag"] == etag(team)
    assert Team.objects.get().name == "platform"


def test_a_member_joining_between_the_read_and_the_lock_is_caught(client, team):
    # The ETag read with the row came before the lock; the one compared is read again under it.
    response = patch(client, f"/underfoot-teams/{team.pk}/", etag(team))

    assert response.status_code == status.HTTP_412_PRECONDITION_FAILED


@pytest.mark.parametrize(("path", "model", "relation"), [("squads", Squad, "members"), ("rosters", Roster, "tags")])
def test_every_strategy_refuses_a_write_made_before_a_relation_changed(client, path, model, relation, bob):
    owner = model.objects.create(name="core")
    related = bob if relation == "members" else Tag.objects.create(label="red")
    read = current_concurrency_etag(model, owner.pk)
    getattr(owner, relation).add(related)

    stale = client.patch(f"/{path}/{owner.pk}/", {"name": "x"}, format="json", headers={"If-Match": read})
    current = client.patch(
        f"/{path}/{owner.pk}/",
        {"name": "x"},
        format="json",
        headers={"If-Match": current_concurrency_etag(model, owner.pk)},
    )

    assert (stale.status_code, current.status_code) == (412, 200)
    assert current["ETag"] == current_concurrency_etag(model, owner.pk)


def test_a_write_made_before_a_row_pointing_at_it_changed_is_refused(client, team):
    read = etag(team)
    TeamNote.objects.create(team=team, text="hello")

    response = patch(client, f"/teams/{team.pk}/", read)

    assert response.status_code == status.HTTP_412_PRECONDITION_FAILED
