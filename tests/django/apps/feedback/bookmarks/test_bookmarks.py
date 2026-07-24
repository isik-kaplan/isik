import pytest
from django.db import connection, models
from django.db.utils import IntegrityError
from django.test import override_settings
from django.test.utils import isolate_apps
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from isik.django.apps.feedback.bookmarks import bookmarks
from isik.django.apps.feedback.votes import votes
from tests.testapp.models import Article, EmailUser, Post


pytestmark = pytest.mark.django_db


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="x")


@pytest.fixture
def post():
    return Post.objects.create(title="hello")


def test_user_related_name_is_required():
    with pytest.raises(TypeError, match="user_related_name"):
        bookmarks()


def test_bookmark_creates_a_row(alice, post):
    alice.bookmark(post)
    assert Post.bookmarks.model.objects.filter(target=post, user=alice).exists()


def test_bookmarking_twice_stays_a_single_row(alice, post):
    alice.bookmark(post)
    alice.bookmark(post)
    assert Post.bookmarks.model.objects.filter(target=post, user=alice).count() == 1


def test_unbookmark_removes_the_row(alice, post):
    alice.bookmark(post)
    alice.unbookmark(post)
    assert not Post.bookmarks.model.objects.filter(target=post, user=alice).exists()


def test_is_bookmarked_reflects_current_state(alice, post):
    assert alice.is_bookmarked(post) is False
    alice.bookmark(post)
    assert alice.is_bookmarked(post) is True
    alice.unbookmark(post)
    assert alice.is_bookmarked(post) is False


def test_toggle_bookmark_flips_state_each_call(alice, post):
    alice.toggle_bookmark(post)
    assert alice.is_bookmarked(post) is True
    alice.toggle_bookmark(post)
    assert alice.is_bookmarked(post) is False


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(actions=st.lists(st.sampled_from(["bookmark", "unbookmark", "toggle"]), max_size=15))
def test_any_sequence_of_actions_never_leaves_more_than_one_row(actions, alice, post):
    for action in actions:
        {"bookmark": alice.bookmark, "unbookmark": alice.unbookmark, "toggle": alice.toggle_bookmark}[action](post)
    assert Post.bookmarks.model.objects.filter(target=post, user=alice).count() <= 1


def test_bookmarking_something_never_attached_raises(alice):
    article = Article.objects.create(title="a")
    with pytest.raises(TypeError, match="not bookmarkable"):
        alice.bookmark(article)


def test_the_unique_constraint_is_enforced_at_the_database_level_too(alice, post):
    Post.bookmarks.model.objects.create(target=post, user=alice)
    with pytest.raises(IntegrityError):
        Post.bookmarks.model.objects.create(target=post, user=alice)


@isolate_apps("tests.testapp")
def test_reusing_a_user_related_name_across_two_bookmarkable_models_raises():
    """Two different bookmarkable host models silently sharing a user_related_name on the User
    model would otherwise clash."""

    class HostA(models.Model):
        class Meta:
            app_label = "testapp"

        bookmarks = bookmarks(user_related_name="shared_bookmark_name")

    with pytest.raises(ValueError, match="already claimed"):

        class HostB(models.Model):
            class Meta:
                app_label = "testapp"

            bookmarks = bookmarks(user_related_name="shared_bookmark_name")


class TestCascadeDelete:
    def test_deleting_the_host_cascades_to_its_bookmarks(self, alice, post):
        alice.bookmark(post)
        post.delete()
        assert not Post.bookmarks.model.objects.filter(user=alice).exists()

    def test_deleting_the_user_cascades_to_their_bookmarks(self, alice, post):
        alice.bookmark(post)
        alice.delete()
        assert not Post.bookmarks.model.objects.filter(target=post).exists()


def _create_tables(*models_):
    """isolate_apps models have no migration - create their tables for the duration of this
    test's transaction (rolled back automatically, like everything else under django_db)."""
    with connection.schema_editor() as editor:
        for model in models_:
            editor.create_model(model)


@isolate_apps("tests.testapp")
def test_reusing_a_target_related_name_on_the_same_host_raises():
    """Two bookmarks() attachments on one host silently sharing a target_related_name would clash
    on the host's own reverse-accessor namespace."""
    with pytest.raises(ValueError, match="already claimed"):

        class HostWithCollidingBookmarkFields(models.Model):
            class Meta:
                app_label = "testapp"

            first = bookmarks(user_related_name="colliding_bookmark_fields_first", target_related_name="shared")
            second = bookmarks(user_related_name="colliding_bookmark_fields_second", target_related_name="shared")


@isolate_apps("tests.testapp")
def test_extra_fields_can_attach_a_maker_to_the_generated_bookmark_model(alice):
    """user_model=EmailUser is explicit rather than the default AUTH_USER_MODEL dotted string:
    isolate_apps registers a fresh, isolated Apps() with only what's defined in this block, and a
    lazy "testapp.EmailUser" string can't resolve against it once real tables are created below."""

    class BookmarkExtraFieldsHost(models.Model):
        class Meta:
            app_label = "testapp"

        bookmarks = bookmarks(
            user_related_name="bookmark_extra_fields_host_bookmarks",
            user_model=EmailUser,
            extra_fields={
                "votes": votes(user_related_name="bookmark_extra_fields_host_bookmark_votes", user_model=EmailUser)
            },
        )

    _create_tables(
        BookmarkExtraFieldsHost,
        BookmarkExtraFieldsHost.bookmarks.model,
        BookmarkExtraFieldsHost.bookmarks.model.votes.model,
    )

    host = BookmarkExtraFieldsHost.objects.create()
    alice.bookmark(host)
    bookmark = BookmarkExtraFieldsHost.bookmarks.model.objects.get(target=host, user=alice)

    assert hasattr(BookmarkExtraFieldsHost.bookmarks.model, "votes")
    alice.upvote(bookmark)
    assert BookmarkExtraFieldsHost.bookmarks.model.votes.model.objects.filter(
        target=bookmark, user=alice, value=1
    ).exists()


class TestBaseModelResolution:
    @isolate_apps("tests.testapp")
    def test_explicit_base_model_kwarg_lets_the_generated_bookmark_model_inherit_a_custom_base(self):
        class ExplicitBookmarkBase(models.Model):
            class Meta:
                app_label = "testapp"
                abstract = True

        class ExplicitBaseBookmarkHost(models.Model):
            class Meta:
                app_label = "testapp"

            bookmarks = bookmarks(
                user_related_name="explicit_base_bookmark_host_bookmarks", base_model=ExplicitBookmarkBase
            )

        assert issubclass(ExplicitBaseBookmarkHost.bookmarks.model, ExplicitBookmarkBase)

    @isolate_apps("tests.testapp")
    def test_feedback_bookmarks_base_model_setting_is_used_when_no_explicit_base_model_is_given(self):
        with override_settings(FEEDBACK_BOOKMARKS_BASE_MODEL="isik.django.apps.common.db.models.BaseModel"):
            from isik.django.apps.common.db.models import BaseModel

            class SettingBaseBookmarkHost(models.Model):
                class Meta:
                    app_label = "testapp"

                bookmarks = bookmarks(user_related_name="setting_base_bookmark_host_bookmarks")

            assert issubclass(SettingBaseBookmarkHost.bookmarks.model, BaseModel)

    @isolate_apps("tests.testapp")
    def test_a_non_abstract_base_model_raises_type_error(self):
        from tests.testapp.models import Widget

        with pytest.raises(TypeError, match="abstract"):

            class NonAbstractBaseBookmarkHost(models.Model):
                class Meta:
                    app_label = "testapp"

                bookmarks = bookmarks(user_related_name="non_abstract_base_bookmark_host_bookmarks", base_model=Widget)
