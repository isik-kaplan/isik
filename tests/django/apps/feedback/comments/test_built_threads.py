"""comments(threaded=..., track_edits=...) building its model, and the serializer made for one - at test time."""

import uuid

import pytest
from django.db import connection, models
from django.test.utils import isolate_apps

from isik.django.apps.feedback.comments import comments
from isik.django.apps.feedback.comments.drf import ReplyCountField, _ThreadedCommentMixin, generic_comment_serializer
from isik.django.drf.serializers.base import BaseModelSerializer
from tests.testapp.models import EmailUser


pytestmark = pytest.mark.django_db


def host_with(**options):
    return type(
        f"Host{uuid.uuid4().hex[:8]}",
        (models.Model,),
        {
            "__module__": __name__,
            "Meta": type("Meta", (), {"app_label": "testapp"}),
            "feedback": comments(user_related_name=f"comments_{uuid.uuid4().hex[:8]}", user_model=EmailUser, **options),
        },
    )


@isolate_apps("tests.testapp")
def test_a_threaded_model_and_its_edit_stamp():
    host = host_with(threaded=True, track_edits=True, max_depth=4, target_name="item")
    model = host.feedback.model
    parent, edited_at = model._meta.get_field("parent"), model._meta.get_field("edited_at")

    assert (host.feedback.config.max_depth, host.feedback.config.target_name) == (4, "item")
    assert (host.feedback.config.threaded, host.feedback.config.track_edits) == (True, True)
    assert parent.remote_field.model is model
    assert (parent.null, parent.blank, parent.remote_field.on_delete) == (True, True, models.CASCADE)
    assert parent.remote_field.related_name == "replies"
    assert str(parent.help_text) == "The comment this one replies to - empty for a top-level comment."
    assert parent.db_comment == "The comment this one replies to - null for a top-level comment."
    assert isinstance(edited_at, models.DateTimeField)
    assert (edited_at.null, edited_at.blank) == (True, True)
    assert str(edited_at.help_text) == "When its author last edited it - empty if never."
    assert edited_at.db_comment == "When its author last edited it - null if never."


@isolate_apps("tests.testapp")
def test_neither_unless_asked():
    model = host_with().feedback.model

    assert {"parent", "edited_at"}.isdisjoint(field.name for field in model._meta.get_fields())
    assert host_with(track_edits=True).feedback.model._meta.get_field("edited_at")
    assert {"parent"}.isdisjoint(field.name for field in host_with(track_edits=True).feedback.model._meta.get_fields())


class TestTheSerializer:
    @isolate_apps("tests.testapp")
    def test_a_threaded_one_with_its_edit_stamp(self):
        model = host_with(threaded=True, track_edits=True).feedback.model

        made = generic_comment_serializer(model, name=f"Made{uuid.uuid4().hex[:8]}")

        assert made.Meta.fields == [
            "id",
            "body",
            "created_at",
            "updated_at",
            "user",
            "parent",
            "reply_count",
            "edited_at",
        ]
        assert made.Meta.read_only_fields == ["user", "created_at", "updated_at", "edited_at"]
        assert made.__mro__[1:3] == (_ThreadedCommentMixin, BaseModelSerializer)
        assert isinstance(made().fields["reply_count"], ReplyCountField)

    @isolate_apps("tests.testapp")
    def test_a_plain_one(self):
        made = generic_comment_serializer(host_with().feedback.model, name=f"Made{uuid.uuid4().hex[:8]}")

        assert made.Meta.fields == ["id", "body", "created_at", "updated_at", "user"]
        assert made.Meta.read_only_fields == ["user", "created_at", "updated_at"]
        assert made.__mro__[1] is BaseModelSerializer

    @isolate_apps("tests.testapp")
    def test_reply_count_reads_the_annotation_or_counts(self, django_assert_num_queries):
        host = host_with(threaded=True)
        model = host.feedback.model
        with connection.schema_editor() as editor:
            editor.create_model(host)
            editor.create_model(model)
        alice = EmailUser.objects.create(username="alice", email="alice@example.com")
        top = model.objects.create(target=host.objects.create(), user=alice, body="top")
        model.objects.create(target=top.target, user=alice, body="reply", parent=top)
        annotated = host.feedback.config.with_reply_counts(model.objects.filter(pk=top.pk)).get()
        field = ReplyCountField()

        with django_assert_num_queries(0):
            assert field.to_representation(annotated) == 1
        assert field.to_representation(model.objects.get(pk=top.pk)) == 1
        assert (field.source, field.read_only) == ("*", True)
