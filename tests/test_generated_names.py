"""Every class isik builds on a caller's behalf takes an optional name - the default stays as it was."""

import pytest
from django.db import models
from django.test.utils import isolate_apps
from rest_framework import serializers

from isik.common.utils import words_to_pascal
from isik.django.apps.feedback.bookmarks import bookmarks
from isik.django.apps.feedback.bookmarks.drf import generic_bookmark_serializer
from isik.django.apps.feedback.comments import comments
from isik.django.apps.feedback.comments.drf import generic_comment_serializer
from isik.django.apps.feedback.notes import notes
from isik.django.apps.feedback.notes.drf import generic_note_serializer
from isik.django.apps.feedback.votes import votes
from isik.django.apps.feedback.votes.drf import generic_vote_serializer
from isik.django.apps.tags.drf import generic_tag_serializer
from isik.django.apps.tags.tags import tags
from isik.django.drf.schema import FakeErrorSerializer
from isik.django.drf.serializers.history import generic_history_serializer
from tests.testapp.models import EmailUser, Widget


class TestWordsToPascal:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("issued_by", "IssuedBy"),
            ("profile.organization", "ProfileOrganization"),
            ("IsSuperUser", "IsSuperUser"),
            ("<lambda>", "Lambda"),
            ("partial_update", "PartialUpdate"),
            ("already", "Already"),
            ("", ""),
        ],
    )
    def test_converts_words_keeping_inner_capitals(self, text, expected):
        assert words_to_pascal(text) == expected


class TestModelMakerNames:
    @isolate_apps("tests.testapp")
    def test_defaults_are_unchanged(self):
        class NamingHost(models.Model):
            class Meta:
                app_label = "testapp"

            votes = votes(user_related_name="naming_host_votes", user_model=EmailUser)
            comments = comments(user_related_name="naming_host_comments", user_model=EmailUser)
            notes = notes(user_related_name="naming_host_notes", user_model=EmailUser)
            bookmarks = bookmarks(user_related_name="naming_host_bookmarks", user_model=EmailUser)
            topics = tags(related_name="naming_host_topics")

        assert NamingHost.votes.model.__name__ == "NamingHostVotesVote"
        assert NamingHost.comments.model.__name__ == "NamingHostCommentsComment"
        assert NamingHost.notes.model.__name__ == "NamingHostNotesNote"
        assert NamingHost.bookmarks.model.__name__ == "NamingHostBookmarksBookmark"
        assert NamingHost.topics.model.__name__ == "NamingHostTopicsTag"
        assert NamingHost.topics.through.__name__ == "NamingHostTopicsObjectTag"

        for factory, model in [
            (generic_vote_serializer, NamingHost.votes.model),
            (generic_comment_serializer, NamingHost.comments.model),
            (generic_note_serializer, NamingHost.notes.model),
            (generic_bookmark_serializer, NamingHost.bookmarks.model),
        ]:
            assert factory(model).__name__ == f"{model.__name__}Serializer"
        assert generic_tag_serializer(NamingHost.topics).__name__ == "NamingHostTopicsTagSerializer"

    @isolate_apps("tests.testapp")
    def test_every_maker_takes_a_model_name(self):
        class NamedHost(models.Model):
            class Meta:
                app_label = "testapp"

            votes = votes(user_related_name="named_host_votes", user_model=EmailUser, model_name="Upvote")
            comments = comments(user_related_name="named_host_comments", user_model=EmailUser, model_name="Remark")
            notes = notes(user_related_name="named_host_notes", user_model=EmailUser, model_name="Jotting")
            bookmarks = bookmarks(user_related_name="named_host_bookmarks", user_model=EmailUser, model_name="Pin")
            topics = tags(related_name="named_host_topics", tag_model_name="Topic", through_model_name="TopicLink")

        assert NamedHost.votes.model.__name__ == "Upvote"
        assert NamedHost.votes.model._meta.db_table == "testapp_upvote"
        # the per-user uniqueness constraint is named after the model too
        assert [c.name for c in NamedHost.votes.model._meta.constraints] == ["unique_upvote_per_user"]
        assert NamedHost.comments.model.__name__ == "Remark"
        assert NamedHost.notes.model.__name__ == "Jotting"
        assert NamedHost.bookmarks.model.__name__ == "Pin"
        assert NamedHost.topics.model.__name__ == "Topic"
        assert NamedHost.topics.through.__name__ == "TopicLink"

        for factory, model in [
            (generic_vote_serializer, NamedHost.votes.model),
            (generic_comment_serializer, NamedHost.comments.model),
            (generic_note_serializer, NamedHost.notes.model),
            (generic_bookmark_serializer, NamedHost.bookmarks.model),
        ]:
            assert factory(model, name=f"{model.__name__}Payload").__name__ == f"{model.__name__}Payload"
        assert generic_tag_serializer(NamedHost.topics, name="TopicPayload").__name__ == "TopicPayload"


@pytest.mark.django_db
class TestSerializerFactoryNames:
    def test_history_serializer(self):
        assert generic_history_serializer(Widget).__name__ == "WidgetHistorySerializer"
        assert generic_history_serializer(Widget, name="WidgetLog").__name__ == "WidgetLog"

    def test_fake_error_serializer(self):
        class ThingSerializer(serializers.Serializer):
            title = serializers.CharField()

        assert FakeErrorSerializer(ThingSerializer, reuse=True).__name__ == "ThingError"
        assert FakeErrorSerializer(ThingSerializer, name="ThingProblems", reuse=True).__name__ == "ThingProblems"
