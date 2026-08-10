import os

import pytest
from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.db.utils import DataError
from django.test import override_settings
from django.test.utils import isolate_apps

from isik.django.apps.common._model_makers import claim_related_name
from isik.django.apps.feedback.comments import comments
from tests.testapp.models import Article, EmailUser, Post


pytestmark = pytest.mark.django_db


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="x")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user(username="bob", email="bob@example.com", password="x")


@pytest.fixture
def post():
    return Post.objects.create(title="hello")


def test_user_related_name_is_required():
    with pytest.raises(TypeError, match="user_related_name"):
        comments()


def test_comment_creates_a_row(alice, post):
    comment = alice.comment(post, "nice post")
    assert comment.body == "nice post"
    assert comment.user == alice


def test_comments_on_returns_every_comment_regardless_of_author(alice, bob, post):
    alice.comment(post, "first")
    bob.comment(post, "second")
    assert Post.comments.model.objects.filter(target=post).count() == 2
    assert {c.body for c in post.comments.all()} == {"first", "second"}


def test_comments_on_mixin_method_returns_every_comment_regardless_of_author(alice, bob, post):
    alice.comment(post, "first")
    bob.comment(post, "second")
    assert {c.body for c in alice.comments_on(post)} == {"first", "second"}


def test_commenting_on_something_never_attached_raises(alice):
    with pytest.raises(TypeError, match="^object is not commentable"):
        alice.comment(object(), "x")


def test_comments_on_something_never_attached_raises(alice):
    with pytest.raises(TypeError, match="^object is not commentable"):
        alice.comments_on(object())


def test_min_length_is_enforced(alice, post):
    comment = Post.comments.model(target=post, user=alice, body="")
    with pytest.raises(ValidationError):
        comment.full_clean()


@isolate_apps("tests.testapp")
def test_comment_min_length_defaults_to_1_not_2(alice):
    # Post.comments was built once at module import time - a mutation to comment_min_length's
    # default wouldn't be observable through a model already constructed before the mutation was
    # selected. A single-character body distinguishes min_length=1 (passes) from min_length=2
    # (fails) - unlike the existing empty-body test above, which fails identically either way.
    # MinLengthHost, not Host - other isolate_apps tests in this file/test_drf.py also define a
    # class literally named Host with the default target_related_name, and
    # claim_related_name()'s registry is keyed by app_label+class name, process-global.
    class MinLengthHost(models.Model):
        class Meta:
            app_label = "testapp"

        notes = comments(user_related_name="min_length_host_comments", user_model=EmailUser)

    with connection.schema_editor() as editor:
        editor.create_model(MinLengthHost)
        editor.create_model(MinLengthHost.notes.model)

    host = MinLengthHost.objects.create()
    comment = MinLengthHost.notes.model(target=host, user=alice, body="x")
    comment.full_clean()


@isolate_apps("tests.testapp")
def test_comments_on_passes_its_own_explicit_field_through_instead_of_re_resolving(alice):
    class Host(models.Model):
        class Meta:
            app_label = "testapp"

        first = comments(
            user_related_name="host_first_comments", target_related_name="first_comments", user_model=EmailUser
        )
        second = comments(
            user_related_name="host_second_comments", target_related_name="second_comments", user_model=EmailUser
        )

    with connection.schema_editor() as editor:
        editor.create_model(Host)
        editor.create_model(Host.first.model)
        editor.create_model(Host.second.model)

    host = Host.objects.create()
    Host.first.model.objects.create(target=host, user=alice, body="hi")
    assert {c.body for c in alice.comments_on(host, field=Host.first)} == {"hi"}


@isolate_apps("tests.testapp")
def test_comment_passes_its_own_explicit_field_through_instead_of_re_resolving(alice):
    class Host(models.Model):
        class Meta:
            app_label = "testapp"

        first = comments(
            user_related_name="host_first_comment_writes",
            target_related_name="first_comment_writes",
            user_model=EmailUser,
        )
        second = comments(
            user_related_name="host_second_comment_writes",
            target_related_name="second_comment_writes",
            user_model=EmailUser,
        )

    with connection.schema_editor() as editor:
        editor.create_model(Host)
        editor.create_model(Host.first.model)
        editor.create_model(Host.second.model)

    host = Host.objects.create()
    alice.comment(host, "hi", field=Host.first)
    assert Host.first.model.objects.filter(target=host, user=alice, body="hi").exists()


class TestVotesAttachedViaExtraFields:
    """Post.comments opts into votes() via extra_fields (see tests/testapp/models.py), generating
    PostCommentVote."""

    def test_the_generated_comment_model_is_marked_voteable(self):
        assert hasattr(Post.comments.model, "votes")

    def test_voting_on_a_comment_works_like_voting_on_any_other_voteable_model(self, alice, post):
        comment = alice.comment(post, "nice post")
        alice.upvote(comment)
        assert Post.comments.model.votes.model.objects.filter(target=comment, user=alice, value=1).exists()

    def test_a_comment_model_that_never_opted_into_votes_is_not_voteable(self, alice):
        article = Article.objects.create(title="a")
        comment = alice.comment(article, "nice article")
        with pytest.raises(TypeError, match="not voteable"):
            alice.upvote(comment)


class TestTiptapEndToEnd:
    """Article.comments opts into tiptap=True with comment_max_length=280 - exercises the
    TiptapValidator through a real model's full_clean()/save(), not called directly."""

    def test_a_valid_tiptap_document_saves_cleanly(self, alice):
        article = Article.objects.create(title="a")
        body = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "nice"}]}]}
        comment = alice.comment(article, body)
        comment.full_clean()
        assert Article.comments.model.objects.get(pk=comment.pk).body == body

    def test_an_invalid_tiptap_document_fails_full_clean(self, alice):
        article = Article.objects.create(title="a")
        body = {"type": "doc", "content": [{"type": "table"}]}
        comment = Article.comments.model(target=article, user=alice, body=body)
        with pytest.raises(ValidationError):
            comment.full_clean()

    def test_max_length_is_enforced_against_extracted_text_not_raw_json(self, alice):
        article = Article.objects.create(title="a")
        long_text = "x" * 300
        body = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": long_text}]}]}
        comment = Article.comments.model(target=article, user=alice, body=body)
        with pytest.raises(ValidationError, match="at most 280"):
            comment.full_clean()

    def test_min_length_is_enforced_against_extracted_text_not_structural_validity(self, alice):
        """A structurally valid but textually empty document (an empty paragraph parses fine)
        still fails full_clean(): min/max length checks run against extracted text, not document
        validity."""
        article = Article.objects.create(title="a")
        body = {"type": "doc", "content": [{"type": "paragraph", "content": []}]}
        comment = Article.comments.model(target=article, user=alice, body=body)
        with pytest.raises(ValidationError, match="at least 1"):
            comment.full_clean()


class TestCascadeDelete:
    def test_deleting_the_host_cascades_to_its_comments(self, alice, post):
        alice.comment(post, "nice post")
        post.delete()
        assert not Post.comments.model.objects.filter(user=alice).exists()

    def test_deleting_the_user_cascades_to_their_comments(self, alice, post):
        alice.comment(post, "nice post")
        alice.delete()
        assert not Post.comments.model.objects.filter(target=post).exists()

    def test_deleting_a_comment_cascades_to_its_own_extra_fields_votes(self, alice, post):
        """Post.comments opts votes() in via extra_fields (tests/testapp/models.py) - the
        generated PostCommentVote's FK to the comment is CASCADE too, one level down."""
        comment = alice.comment(post, "nice post")
        alice.upvote(comment)
        comment.delete()
        assert not Post.comments.model.votes.model.objects.filter(user=alice).exists()

    def test_deleting_the_host_transitively_cascades_through_comments_to_their_votes(self, alice, post):
        comment = alice.comment(post, "nice post")
        alice.upvote(comment)
        post.delete()
        assert not Post.comments.model.objects.filter(pk=comment.pk).exists()
        assert not Post.comments.model.votes.model.objects.filter(user=alice).exists()


def _create_tables(*models_):
    """isolate_apps models have no migration - create their tables for the duration of this
    test's transaction (rolled back automatically, like everything else under django_db)."""
    with connection.schema_editor() as editor:
        for model in models_:
            editor.create_model(model)


@isolate_apps("tests.testapp")
def test_reusing_a_user_related_name_across_two_commentable_models_raises():
    """Two different commentable host models silently sharing a user_related_name on the User
    model would otherwise clash."""

    class HostA(models.Model):
        class Meta:
            app_label = "testapp"

        comments = comments(user_related_name="shared_comment_name")

    with pytest.raises(ValueError, match="already claimed"):

        class HostB(models.Model):
            class Meta:
                app_label = "testapp"

            comments = comments(user_related_name="shared_comment_name")


@isolate_apps("tests.testapp")
def test_reusing_a_target_related_name_on_the_same_host_raises():
    """Two comments() attachments on one host silently sharing a target_related_name would clash
    on the host's own reverse-accessor namespace."""
    with pytest.raises(ValueError, match="already claimed"):

        class HostWithCollidingCommentFields(models.Model):
            class Meta:
                app_label = "testapp"

            first = comments(user_related_name="colliding_comment_fields_first", target_related_name="shared")
            second = comments(user_related_name="colliding_comment_fields_second", target_related_name="shared")


@isolate_apps("tests.testapp")
def test_defaults_and_wiring():
    class DefaultCommentsHost(models.Model):
        class Meta:
            app_label = "testapp"

        entries = comments(user_related_name="host_comment_entries", user_model=EmailUser)

    # target_name defaults to "target"
    target_field = DefaultCommentsHost.entries.model._meta.get_field("target")
    assert target_field.related_model is DefaultCommentsHost

    # target_related_name defaults to "comments" - claim_related_name() actually recorded it
    with pytest.raises(ValueError, match="already claimed"):
        claim_related_name(DefaultCommentsHost, "comments", "somebody-else")

    # the user FK's related_name is wired to user_related_name, not dropped/None
    user_field = DefaultCommentsHost.entries.model._meta.get_field("user")
    assert user_field.remote_field.related_name == "host_comment_entries"


class TestBaseModelResolution:
    @isolate_apps("tests.testapp")
    def test_explicit_base_model_kwarg_lets_the_generated_comment_model_inherit_a_custom_base(self):
        class ExplicitCommentBase(models.Model):
            class Meta:
                app_label = "testapp"
                abstract = True

        class ExplicitBaseCommentHost(models.Model):
            class Meta:
                app_label = "testapp"

            comments = comments(user_related_name="explicit_base_comment_host_comments", base_model=ExplicitCommentBase)

        assert issubclass(ExplicitBaseCommentHost.comments.model, ExplicitCommentBase)

    @isolate_apps("tests.testapp")
    def test_feedback_comments_base_model_setting_is_used_when_no_explicit_base_model_is_given(self):
        with override_settings(FEEDBACK_COMMENTS_BASE_MODEL="isik.django.apps.common.db.models.BaseModel"):
            from isik.django.apps.common.db.models import BaseModel

            class SettingBaseCommentHost(models.Model):
                class Meta:
                    app_label = "testapp"

                comments = comments(user_related_name="setting_base_comment_host_comments")

            assert issubclass(SettingBaseCommentHost.comments.model, BaseModel)

    @isolate_apps("tests.testapp")
    def test_a_non_abstract_base_model_raises_type_error(self):
        from tests.testapp.models import Widget

        with pytest.raises(TypeError, match="abstract"):

            class NonAbstractBaseCommentHost(models.Model):
                class Meta:
                    app_label = "testapp"

                comments = comments(user_related_name="non_abstract_base_comment_host_comments", base_model=Widget)


class TestPlainTextMaxLength:
    """Post.comments has no comment_max_length configured, and Article's only max-length
    coverage goes through the tiptap branch (comments.tiptap.TiptapValidator) - these exercise
    the plain-text comment_max_length branch directly."""

    def test_no_comment_max_length_leaves_the_field_an_unbounded_text_field(self):
        body_field = Post.comments.model._meta.get_field("body")
        assert body_field.max_length is None
        assert isinstance(body_field, models.TextField)

    @isolate_apps("tests.testapp")
    def test_comment_max_length_kwarg_sets_the_generated_fields_max_length(self):
        class MaxLengthCommentHost(models.Model):
            class Meta:
                app_label = "testapp"

            comments = comments(
                user_related_name="max_length_comment_host_field_comments",
                user_model=EmailUser,
                comment_max_length=10,
            )

        body_field = MaxLengthCommentHost.comments.model._meta.get_field("body")
        assert body_field.max_length == 10
        assert isinstance(body_field, models.CharField)

    @isolate_apps("tests.testapp")
    def test_a_body_over_the_configured_max_length_fails_full_clean(self, alice):
        class MaxLengthCommentHost(models.Model):
            class Meta:
                app_label = "testapp"

            comments = comments(
                user_related_name="max_length_comment_host_comments", user_model=EmailUser, comment_max_length=10
            )

        _create_tables(MaxLengthCommentHost, MaxLengthCommentHost.comments.model)
        host = MaxLengthCommentHost.objects.create()

        comment = MaxLengthCommentHost.comments.model(target=host, user=alice, body="x" * 11)
        with pytest.raises(ValidationError):
            comment.full_clean()

    @isolate_apps("tests.testapp")
    def test_a_body_at_exactly_the_max_length_is_valid(self, alice):
        class MaxLengthCommentHost(models.Model):
            class Meta:
                app_label = "testapp"

            comments = comments(
                user_related_name="max_length_comment_host_comments", user_model=EmailUser, comment_max_length=10
            )

        _create_tables(MaxLengthCommentHost, MaxLengthCommentHost.comments.model)
        host = MaxLengthCommentHost.objects.create()

        comment = MaxLengthCommentHost.comments.model(target=host, user=alice, body="x" * 10)
        comment.full_clean()

    @isolate_apps("tests.testapp")
    def test_min_length_is_still_enforced_when_comment_max_length_is_also_set(self, alice):
        # The CharField branch (comment_max_length set) builds its own validators list separately
        # from the TextField branch - dropping it there wouldn't be caught by the plain
        # test_min_length_is_enforced (unbounded, TextField branch) above.
        class MaxLengthCommentHost(models.Model):
            class Meta:
                app_label = "testapp"

            comments = comments(
                user_related_name="min_length_with_max_length_host_comments",
                user_model=EmailUser,
                comment_max_length=10,
            )

        _create_tables(MaxLengthCommentHost, MaxLengthCommentHost.comments.model)
        host = MaxLengthCommentHost.objects.create()

        comment = MaxLengthCommentHost.comments.model(target=host, user=alice, body="")
        with pytest.raises(ValidationError):
            comment.full_clean()

    @isolate_apps("tests.testapp")
    def test_a_body_over_the_configured_max_length_is_rejected_at_the_database_level(self, alice):
        class MaxLengthCommentHost(models.Model):
            class Meta:
                app_label = "testapp"

            comments = comments(
                user_related_name="max_length_comment_host_db_comments", user_model=EmailUser, comment_max_length=10
            )

        _create_tables(MaxLengthCommentHost, MaxLengthCommentHost.comments.model)
        host = MaxLengthCommentHost.objects.create()

        # Bypasses full_clean() entirely, same as comment() does - the varchar(10) column itself,
        # not application code, is what stops this.
        with pytest.raises(DataError):
            MaxLengthCommentHost.comments.model.objects.create(target=host, user=alice, body="x" * 11)


class TestPerCallTiptapSchemaPath:
    """tiptap_schema_path= must reach the TiptapValidator comments() builds, not just fall back to
    FEEDBACK_COMMENTS_TIPTAP_SCHEMA_PATH."""

    @isolate_apps("tests.testapp")
    def test_a_document_valid_under_the_full_schema_but_not_the_narrower_per_call_schema_fails(self, alice):
        minimal_schema_path = os.path.join(
            os.path.dirname(django_settings.FEEDBACK_COMMENTS_TIPTAP_SCHEMA_PATH), "tiptap_schema_minimal.json"
        )

        class NarrowSchemaCommentHost(models.Model):
            class Meta:
                app_label = "testapp"

            comments = comments(
                user_related_name="narrow_schema_comment_host_comments",
                user_model=EmailUser,
                tiptap=True,
                tiptap_schema_path=minimal_schema_path,
            )

        _create_tables(NarrowSchemaCommentHost, NarrowSchemaCommentHost.comments.model)
        host = NarrowSchemaCommentHost.objects.create()

        body = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "cc "}, {"type": "mention", "attrs": {"id": "user:1"}}],
                }
            ],
        }
        comment = NarrowSchemaCommentHost.comments.model(target=host, user=alice, body=body)
        with pytest.raises(ValidationError, match="Unknown node type: mention"):
            comment.full_clean()
