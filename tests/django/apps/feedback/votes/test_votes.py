import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.db.utils import IntegrityError
from django.test import override_settings
from django.test.utils import isolate_apps
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from isik.django.apps.feedback.bookmarks import bookmarks
from isik.django.apps.feedback.votes import UserVoteMixin, votes
from tests.testapp.models import Article, EmailUser, MultiVoteHost, Post


pytestmark = pytest.mark.django_db


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="x")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user(username="bob", email="bob@example.com", password="x")


@pytest.fixture
def post(alice):
    return Post.objects.create(title="hello")


def test_user_related_name_is_required():
    with pytest.raises(TypeError, match="user_related_name"):
        votes()


def test_post_is_marked_voteable_with_the_generated_model():
    assert {f.name for f in Post.votes.model._meta.get_fields()} >= {"id", "target", "user", "value", "created_at"}


def test_upvote_creates_a_row_with_value_one(alice, post):
    alice.upvote(post)
    vote = Post.votes.model.objects.get(target=post, user=alice)
    assert vote.value == 1


def test_downvote_creates_a_row_with_value_minus_one(alice, post):
    alice.downvote(post)
    vote = Post.votes.model.objects.get(target=post, user=alice)
    assert vote.value == -1


def test_upvoting_twice_stays_a_single_row(alice, post):
    alice.upvote(post)
    alice.upvote(post)
    assert Post.votes.model.objects.filter(target=post, user=alice).count() == 1


def test_switching_from_upvote_to_downvote_updates_the_same_row(alice, post):
    alice.upvote(post)
    alice.downvote(post)
    assert Post.votes.model.objects.filter(target=post, user=alice).count() == 1
    assert Post.votes.model.objects.get(target=post, user=alice).value == -1


def test_unvote_removes_the_row(alice, post):
    alice.upvote(post)
    alice.unvote(post)
    assert not Post.votes.model.objects.filter(target=post, user=alice).exists()


def test_unvote_without_a_prior_vote_is_a_no_op(alice, post):
    alice.unvote(post)
    assert not Post.votes.model.objects.filter(target=post, user=alice).exists()


def test_two_different_users_each_get_their_own_row(alice, bob, post):
    alice.upvote(post)
    bob.downvote(post)
    assert Post.votes.model.objects.filter(target=post).count() == 2


def test_voting_on_something_that_never_had_votes_attached_raises(alice):
    article = Article.objects.create(title="a")
    with pytest.raises(TypeError, match="not voteable"):
        alice.upvote(article)


def test_voting_on_a_plain_object_raises(alice):
    with pytest.raises(TypeError, match="not voteable"):
        alice.upvote(object())


def test_the_unique_constraint_is_enforced_at_the_database_level_too(alice, post):
    Post.votes.model.objects.create(target=post, user=alice, value=1)
    with pytest.raises(IntegrityError):
        Post.votes.model.objects.create(target=post, user=alice, value=-1)


def test_a_value_outside_the_choices_fails_full_clean(alice, post):
    vote = Post.votes.model(target=post, user=alice, value=5)
    with pytest.raises(ValidationError, match="not a valid choice"):
        vote.full_clean()


@isolate_apps("tests.testapp")
def test_reusing_a_user_related_name_across_two_voteable_models_raises():
    class HostA(models.Model):
        class Meta:
            app_label = "testapp"

        votes = votes(user_related_name="shared_name")

    with pytest.raises(ValueError, match="already claimed"):

        class HostB(models.Model):
            class Meta:
                app_label = "testapp"

            votes = votes(user_related_name="shared_name")


class TestMultipleAttachmentsOnTheSameHost:
    """MultiVoteHost attaches votes() twice (upvotes/helpfulness) - see tests/testapp/models.py."""

    @pytest.fixture
    def host(self):
        return MultiVoteHost.objects.create()

    def test_ambiguous_call_without_field_raises(self, alice, host):
        with pytest.raises(TypeError, match="multiple voteable fields"):
            alice.upvote(host)

    def test_field_disambiguates_which_attachment_is_used(self, alice, host):
        alice.upvote(host, field=MultiVoteHost.upvotes)
        alice.downvote(host, field=MultiVoteHost.helpfulness)

        assert MultiVoteHost.upvotes.model.objects.get(target=host, user=alice).value == 1
        assert MultiVoteHost.helpfulness.model.objects.get(target=host, user=alice).value == -1

    def test_the_two_attachments_have_entirely_separate_generated_models(self):
        assert MultiVoteHost.upvotes.model is not MultiVoteHost.helpfulness.model


class TestUserVoteMixinIsRequiredExplicitly:
    def test_a_plain_class_without_the_mixin_has_no_vote_verbs(self):
        assert not hasattr(object(), "upvote")

    def test_mixing_it_in_adds_the_verbs(self):
        class Actor(UserVoteMixin):
            pass

        assert hasattr(Actor(), "upvote")
        assert hasattr(Actor(), "downvote")
        assert hasattr(Actor(), "unvote")


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(actions=st.lists(st.sampled_from(["up", "down", "un"]), max_size=15))
def test_any_sequence_of_actions_never_leaves_more_than_one_row(actions, alice, post):
    for action in actions:
        {"up": alice.upvote, "down": alice.downvote, "un": alice.unvote}[action](post)
    assert Post.votes.model.objects.filter(target=post, user=alice).count() <= 1


class TestCascadeDelete:
    def test_deleting_the_host_cascades_to_its_votes(self, alice, post):
        alice.upvote(post)
        post.delete()
        assert not Post.votes.model.objects.filter(user=alice).exists()

    def test_deleting_the_user_cascades_to_their_votes(self, alice, post):
        alice.upvote(post)
        alice.delete()
        assert not Post.votes.model.objects.filter(target=post).exists()


@isolate_apps("tests.testapp")
def test_reusing_a_target_related_name_on_the_same_host_raises():
    """Two votes() attachments on one host silently sharing a target_related_name would clash on
    the host's own reverse-accessor namespace."""
    with pytest.raises(ValueError, match="already claimed"):

        class HostWithCollidingVoteFields(models.Model):
            class Meta:
                app_label = "testapp"

            first = votes(user_related_name="colliding_vote_fields_first", target_related_name="shared")
            second = votes(user_related_name="colliding_vote_fields_second", target_related_name="shared")


def _create_tables(*models_):
    """isolate_apps models have no migration - create their tables for the duration of this
    test's transaction (rolled back automatically, like everything else under django_db)."""
    with connection.schema_editor() as editor:
        for model in models_:
            editor.create_model(model)


@isolate_apps("tests.testapp")
def test_extra_fields_can_attach_a_maker_to_the_generated_vote_model(alice):
    """votes() merges other makers' fields in via extra_fields=, per its own docstring example
    (`votes(extra_fields={"bookmarks": bookmarks(...)})`).

    user_model=EmailUser is explicit rather than the default AUTH_USER_MODEL dotted string:
    isolate_apps registers a fresh, isolated Apps() with only what's defined in this block, and a
    lazy "testapp.EmailUser" string can't resolve against it once real tables are created below."""

    class VoteExtraFieldsHost(models.Model):
        class Meta:
            app_label = "testapp"

        votes = votes(
            user_related_name="vote_extra_fields_host_votes",
            user_model=EmailUser,
            extra_fields={
                "bookmarks": bookmarks(user_related_name="vote_extra_fields_host_vote_bookmarks", user_model=EmailUser)
            },
        )

    _create_tables(
        VoteExtraFieldsHost, VoteExtraFieldsHost.votes.model, VoteExtraFieldsHost.votes.model.bookmarks.model
    )

    host = VoteExtraFieldsHost.objects.create()
    alice.upvote(host)
    vote = VoteExtraFieldsHost.votes.model.objects.get(target=host, user=alice)

    assert hasattr(VoteExtraFieldsHost.votes.model, "bookmarks")
    alice.bookmark(vote)
    assert VoteExtraFieldsHost.votes.model.bookmarks.model.objects.filter(target=vote, user=alice).exists()


class TestBaseModelResolution:
    @isolate_apps("tests.testapp")
    def test_explicit_base_model_kwarg_lets_the_generated_vote_model_inherit_a_custom_base(self):
        class ExplicitVoteBase(models.Model):
            class Meta:
                app_label = "testapp"
                abstract = True

        class ExplicitBaseVoteHost(models.Model):
            class Meta:
                app_label = "testapp"

            votes = votes(user_related_name="explicit_base_vote_host_votes", base_model=ExplicitVoteBase)

        assert issubclass(ExplicitBaseVoteHost.votes.model, ExplicitVoteBase)

    @isolate_apps("tests.testapp")
    def test_feedback_votes_base_model_setting_is_used_when_no_explicit_base_model_is_given(self):
        with override_settings(FEEDBACK_VOTES_BASE_MODEL="isik.django.apps.common.db.models.BaseModel"):
            from isik.django.apps.common.db.models import BaseModel

            class SettingBaseVoteHost(models.Model):
                class Meta:
                    app_label = "testapp"

                votes = votes(user_related_name="setting_base_vote_host_votes")

            assert issubclass(SettingBaseVoteHost.votes.model, BaseModel)

    @isolate_apps("tests.testapp")
    def test_a_non_abstract_base_model_raises_type_error(self):
        from tests.testapp.models import Widget

        with pytest.raises(TypeError, match="abstract"):

            class NonAbstractBaseVoteHost(models.Model):
                class Meta:
                    app_label = "testapp"

                votes = votes(user_related_name="non_abstract_base_vote_host_votes", base_model=Widget)
