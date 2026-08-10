import pytest
from django.db import connection, models
from django.test.utils import isolate_apps
from rest_framework.test import APIRequestFactory

from isik.django.apps.feedback.notes import notes
from isik.django.apps.feedback.notes.drf import generic_note_serializer
from isik.django.drf.permissions import is_owner
from isik.django.drf.viewsets.base import BaseModelViewSet
from tests.testapp.models import EmailUser, Post


pytestmark = pytest.mark.django_db


# Built once at module scope, like real usage (`NoteSerializer = generic_note_serializer(...)`) -
# ModelSerializerRegistryMixin raises if the same model is registered twice, so calling the
# factory fresh in every test would collide on the second call.
NoteSerializer = generic_note_serializer(Post.notes.model)


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="x")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user(username="bob", email="bob@example.com", password="x")


@pytest.fixture
def post():
    return Post.objects.create(title="hello")


def test_body_is_writable_user_is_read_only(alice, post):
    note = alice.add_note(post, "hi")
    fields = NoteSerializer().fields
    assert fields["body"].read_only is False
    assert fields["user"].read_only is True
    assert NoteSerializer(note).data["body"] == "hi"


def test_composes_with_is_owner_for_a_private_notes_viewset(alice, bob, post):
    """generic_note_serializer() and is_owner() compose without notes() needing to know about
    permissions at all."""
    alice_note = alice.add_note(post, "alice's private note")

    class NoteViewSet(BaseModelViewSet):
        # exempt_from_registry - see bookmarks/test_drf.py's BookmarkViewSet for why.
        model = Post.notes.model
        endpoint = "notes"
        serializer_class = NoteSerializer
        permission_classes = [is_owner("user")]
        exempt_from_registry = True

        def get_queryset(self):
            return self.model.objects.filter(target=post)

    factory = APIRequestFactory()

    def call(method, action, user, **kwargs):
        request = getattr(factory, method)(f"/notes/{alice_note.pk}/", **kwargs)
        request.user = user
        return NoteViewSet.as_view({method: action})(request, pk=alice_note.pk)

    assert call("get", "retrieve", alice).status_code == 200
    assert call("get", "retrieve", bob).status_code == 403

    response = call("patch", "partial_update", bob, data={"body": "hacked by bob"}, format="json")
    assert response.status_code == 403
    assert Post.notes.model.objects.get(pk=alice_note.pk).body == "alice's private note"

    response = call("patch", "partial_update", alice, data={"body": "edited by alice"}, format="json")
    assert response.status_code == 200
    assert Post.notes.model.objects.get(pk=alice_note.pk).body == "edited by alice"

    response = call("put", "update", bob, data={"body": "hacked by bob"}, format="json")
    assert response.status_code == 403
    assert Post.notes.model.objects.get(pk=alice_note.pk).body == "edited by alice"

    response = call("put", "update", alice, data={"body": "edited again by alice"}, format="json")
    assert response.status_code == 200
    assert Post.notes.model.objects.get(pk=alice_note.pk).body == "edited again by alice"

    response = call("delete", "destroy", bob)
    assert response.status_code == 403
    assert Post.notes.model.objects.filter(pk=alice_note.pk).exists()

    response = call("delete", "destroy", alice)
    assert response.status_code == 204
    assert not Post.notes.model.objects.filter(pk=alice_note.pk).exists()


class TestBodyMaxLengthEnforcement:
    """DRF's ModelSerializer derives a length-validating field from body's own CharField
    max_length, so the API rejects an over-long body before it reaches the database."""

    @isolate_apps("tests.testapp")
    def test_a_body_over_the_configured_max_length_is_rejected_by_the_serializer(self):
        class MaxLengthNoteHost(models.Model):
            class Meta:
                app_label = "testapp"

            notes = notes(user_related_name="max_length_note_host_notes", user_model=EmailUser, body_max_length=5)

        with connection.schema_editor() as editor:
            editor.create_model(MaxLengthNoteHost)
            editor.create_model(MaxLengthNoteHost.notes.model)

        Serializer = generic_note_serializer(MaxLengthNoteHost.notes.model)
        serializer = Serializer(data={"body": "x" * 6})
        assert not serializer.is_valid()
        assert "body" in serializer.errors

    @isolate_apps("tests.testapp")
    def test_a_body_at_exactly_the_max_length_is_accepted(self):
        class MaxLengthNoteHost(models.Model):
            class Meta:
                app_label = "testapp"

            notes = notes(user_related_name="max_length_note_host_notes", user_model=EmailUser, body_max_length=5)

        with connection.schema_editor() as editor:
            editor.create_model(MaxLengthNoteHost)
            editor.create_model(MaxLengthNoteHost.notes.model)

        Serializer = generic_note_serializer(MaxLengthNoteHost.notes.model)
        serializer = Serializer(data={"body": "x" * 5})
        assert serializer.is_valid(), serializer.errors


@isolate_apps("tests.testapp")
def test_meta_fields_and_read_only_fields_are_exact():
    # A fresh Host, not Post.notes - Post-based NoteSerializer is built once at module scope (see
    # the comment above) and would never observe a broken generic_note_serializer() under a tool
    # that reruns pytest in the same process (e.g. mutation testing).
    class Host(models.Model):
        class Meta:
            app_label = "testapp"

        notes = notes(user_related_name="host_notes")

    Serializer = generic_note_serializer(Host.notes.model)
    assert Serializer.Meta.fields == ["id", "body", "created_at", "updated_at", "user"]
    assert Serializer.Meta.read_only_fields == ["user", "created_at", "updated_at"]
    assert Serializer.Meta.model is Host.notes.model
    assert Serializer.__name__ == f"{Host.notes.model.__name__}Serializer"
