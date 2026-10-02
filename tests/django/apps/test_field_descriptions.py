"""
Every field isik declares or generates says what it holds - a translatable `help_text` for the API and
a plain `db_comment` for the database - so a project holding its own models to "every column is
described" has nothing of isik's to exempt.

The hosts are built inside each test, under `isolate_apps`, rather than read off testapp's models: a
maker runs when its host class is defined, and testapp's are defined once, at import.
"""

import pytest
from django.core.validators import MinLengthValidator
from django.db import models
from django.test.utils import isolate_apps
from django.utils import translation

from isik.django.apps.common.fields.gfk import AutoGenericForeignKey
from isik.django.apps.feedback.bookmarks import bookmarks
from isik.django.apps.feedback.comments import comments
from isik.django.apps.feedback.comments import comments as comments_maker
from isik.django.apps.feedback.notes import notes
from isik.django.apps.feedback.votes import votes
from isik.django.apps.idempotency.by_reference.models import IdempotencyClaim
from isik.django.apps.idempotency.with_body.models import IdempotencyClaimWithBody
from isik.django.apps.tags.tags import tags
from tests.testapp.models import EmailUser, Widget


def described(model, *names):
    """{name: (help_text, db_comment)} for `names` - every concrete field when none are named."""
    fields = [model._meta.get_field(name) for name in names] or model._meta.concrete_fields
    return {field.name: (str(field.help_text), field.db_comment) for field in fields}


def both(text):
    return (text, text)


MAKER_BASE = {"created_at": both("When the row was created."), "updated_at": both("When the row last changed.")}


@pytest.fixture
def host():
    """A host with every maker on it, its unbounded variants - built fresh, in an isolated registry."""
    with isolate_apps("tests.testapp"):

        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            votes = votes(user_related_name="described_host_votes", user_model=EmailUser)
            bookmarks = bookmarks(user_related_name="described_host_bookmarks", user_model=EmailUser)
            comments = comments(user_related_name="described_host_comments", user_model=EmailUser)
            notes = notes(user_related_name="described_host_notes", user_model=EmailUser)
            topics = tags(related_name="described_hosts")
            subject = AutoGenericForeignKey()

        yield Host


@pytest.fixture
def bounded_host():
    """The variants a length bound or a Tiptap schema switches the body field to."""
    with isolate_apps("tests.testapp"):

        class BoundedHost(models.Model):
            class Meta:
                app_label = "testapp"

            notes = notes(user_related_name="bounded_host_notes", user_model=EmailUser, body_max_length=10)
            comments = comments(user_related_name="bounded_host_comments", user_model=EmailUser, comment_max_length=10)
            documents = comments_maker(
                user_related_name="bounded_host_documents",
                target_related_name="documents",
                user_model=EmailUser,
                tiptap=True,
            )

        yield BoundedHost


def test_a_vote_says_what_it_holds(host):
    assert described(host.votes.model, *MAKER_BASE, "target", "user", "value") == {
        **MAKER_BASE,
        "target": both("The Host this vote is on."),
        "user": both("Who cast this vote."),
        "value": both("1 for an upvote, -1 for a downvote."),
    }


def test_a_bookmark_says_what_it_holds(host):
    assert described(host.bookmarks.model, *MAKER_BASE, "target", "user") == {
        **MAKER_BASE,
        "target": both("The Host bookmarked."),
        "user": both("Who bookmarked it."),
    }


def test_a_comment_says_what_it_holds(host):
    assert described(host.comments.model, *MAKER_BASE, "target", "user", "body") == {
        **MAKER_BASE,
        "target": both("The Host this comment is on."),
        "user": both("Who wrote this comment."),
        "body": both("The comment's text."),
    }


def test_a_note_says_what_it_holds(host):
    assert described(host.notes.model, "target", "user", "body") == {
        "target": both("The Host this note is on."),
        "user": both("Who wrote this note."),
        "body": both("The note's text."),
    }


def test_a_tag_and_its_application_say_what_they_hold(host):
    assert described(host.topics.model, *MAKER_BASE, "name") == {
        **MAKER_BASE,
        "name": both("The tag's name, unique in its pool."),
    }
    assert described(host.topics.through, "tag", "target") == {
        "tag": both("The tag applied."),
        "target": both("The Host tagged."),
    }


def test_a_tag_pool_says_what_it_is_though_it_has_no_column(host):
    # A many-to-many has no column of its own to comment - its through model's columns say it.
    assert described(host, "topics") == {"topics": ("Tags from this pool.", None)}


def test_a_generic_foreign_keys_columns_name_it(host):
    assert described(host, "subject_content_type", "subject_object_id") == {
        "subject_content_type": both("The type of the subject object."),
        "subject_object_id": both("The primary key of the subject object."),
    }


def test_a_generic_foreign_keys_own_column_options_win(host):
    with isolate_apps("tests.testapp"):

        class Explained(models.Model):
            class Meta:
                app_label = "testapp"

            subject = AutoGenericForeignKey(object_id_field_kwargs={"help_text": "Ours.", "db_comment": "Ours."})

    assert described(Explained, "subject_object_id") == {"subject_object_id": both("Ours.")}


def test_the_bodies_keep_their_validators_and_bounds(host, bounded_host):
    unbounded = host.comments.model._meta.get_field("body")
    bounded = bounded_host.comments.model._meta.get_field("body")
    bounded_note = bounded_host.notes.model._meta.get_field("body")

    assert (type(unbounded), [type(v) for v in unbounded.validators]) == (models.TextField, [MinLengthValidator])
    assert (type(bounded), bounded.max_length) == (models.CharField, 10)
    assert MinLengthValidator in [type(v) for v in bounded.validators]
    assert (type(bounded_note), bounded_note.max_length) == (models.CharField, 10)


def test_every_body_variant_says_what_it_holds(bounded_host):
    assert described(bounded_host.notes.model, "body") == {"body": both("The note's text.")}
    assert described(bounded_host.comments.model, "body") == {"body": both("The comment's text.")}
    assert described(bounded_host.documents.model, "body") == {"body": both("The comment, as a Tiptap document.")}


def test_every_column_isik_generates_is_described(host, bounded_host):
    generated = [
        host.votes.model,
        host.bookmarks.model,
        host.comments.model,
        host.notes.model,
        host.topics.model,
        host.topics.through,
        bounded_host.notes.model,
        bounded_host.comments.model,
        bounded_host.documents.model,
    ]
    # A maker-built model's automatic `id` is Django's own (DEFAULT_AUTO_FIELD), not isik's.
    undescribed = [
        f"{model.__name__}.{name}"
        for model in generated
        for name, (help_text, db_comment) in described(model).items()
        if name != "id" and not (help_text and db_comment)
    ]
    assert undescribed == []


def test_the_base_models_fields_say_what_they_hold():
    assert described(Widget, "id", "created_at", "updated_at") == {
        "id": both("A random identifier, assigned when the row is created."),
        "created_at": (
            "When the row was created, by the database's clock. Never changes.",
            "When the row was created, by the database's clock. A trigger refuses any change to it.",
        ),
        "updated_at": (
            "When the row last changed, by the database's clock.",
            "When the row last changed, by the database's clock. Stamped by a trigger on every update.",
        ),
    }


@pytest.mark.parametrize("model", [IdempotencyClaim, IdempotencyClaimWithBody])
def test_every_claim_column_is_described(model):
    assert all(help_text and db_comment for help_text, db_comment in described(model).values())


def test_help_text_is_translated_when_its_read(host):
    field = host.votes.model._meta.get_field("user")

    with translation.override("en"):
        assert str(field.help_text) == "Who cast this vote."
    assert type(field.help_text) is not str
