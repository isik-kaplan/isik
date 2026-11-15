"""ClientPrimaryKeyMixin - a row created under the id its client made."""

import uuid

import pytest
from django.db import IntegrityError, transaction

from isik.django.apps.sync.drf import PrimaryKeyTaken
from tests.django.apps.sync import support
from tests.testapp.models import SyncTask
from tests.testapp.sync_views import TaskSerializer


pytestmark = pytest.mark.django_db

ALICE = "0190a000-0000-7000-8000-a00000000001"
TASK = "0190a000-0000-7000-8000-100000000001"


def made(**data):
    support.user(ALICE)
    serializer = TaskSerializer(data={"owner": ALICE, "title": "t", **data})
    serializer.is_valid(raise_exception=True)
    return serializer.save()


def test_a_create_takes_the_client_s_id():
    assert str(made(id=TASK).pk) == TASK


def test_a_create_without_one_makes_one():
    assert isinstance(made().pk, uuid.UUID)


def test_an_id_taken_is_a_409():
    made(id=TASK)

    with pytest.raises(PrimaryKeyTaken) as raised:
        made(id=TASK)

    assert (raised.value.status_code, raised.value.default_code) == (409, "primary_key_taken")
    assert str(raised.value.detail) == "A row with this id exists already."


def test_an_id_taken_fails_only_its_own_savepoint():
    made(id=TASK)

    with transaction.atomic():
        with pytest.raises(PrimaryKeyTaken):
            made(id=TASK)
        assert SyncTask.objects.count() == 1


def _taken(self, validated_data):
    raise IntegrityError("duplicate key")


def test_another_integrity_error_is_left_to_raise(monkeypatch):
    monkeypatch.setattr("rest_framework.serializers.ModelSerializer.create", _taken)

    with pytest.raises(IntegrityError):
        made(id=TASK)
    with pytest.raises(IntegrityError):
        made()


def test_after_the_create_the_id_is_read_only():
    task = made(id=TASK)
    serializer = TaskSerializer(task, data={"id": str(uuid.uuid4()), "title": "u"}, partial=True)
    serializer.is_valid(raise_exception=True)

    assert str(serializer.save().pk) == TASK
    assert serializer.fields["id"].read_only
