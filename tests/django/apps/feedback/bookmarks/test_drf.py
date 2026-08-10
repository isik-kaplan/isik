import pytest
from rest_framework.test import APIRequestFactory

from isik.django.apps.feedback.bookmarks.drf import generic_bookmark_serializer
from isik.django.drf.permissions import is_owner
from isik.django.drf.viewsets.base import BaseModelViewSet
from tests.testapp.models import Post


pytestmark = pytest.mark.django_db


# Built once at module scope - ModelSerializerRegistryMixin raises if the same model is
# registered twice, so calling the factory fresh in every test would collide on the second call.
BookmarkSerializer = generic_bookmark_serializer(Post.bookmarks.model)


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="x")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user(username="bob", email="bob@example.com", password="x")


@pytest.fixture
def post():
    return Post.objects.create(title="hello")


def test_user_is_read_only(alice, post):
    bookmark = Post.bookmarks.model.objects.create(target=post, user=alice)
    data = BookmarkSerializer(bookmark).data
    assert data["user"] == alice.pk
    assert BookmarkSerializer().fields["user"].read_only is True


def test_composes_with_is_owner_for_a_private_bookmark_viewset(alice, bob, post):
    """Every field on the generated bookmark model is read-only, so there's no update/
    partial_update to exercise - only retrieve and destroy."""
    alice.bookmark(post)
    alice_bookmark = Post.bookmarks.model.objects.get(target=post, user=alice)

    class BookmarkViewSet(BaseModelViewSet):
        # exempt_from_registry - this class body re-executes every time the test runs, and the
        # registry is process-global; without this, a second in-process run of this test (e.g. a
        # mutation-testing tool re-invoking pytest without restarting the process) would collide
        # with itself instead of the actual host application.
        model = Post.bookmarks.model
        endpoint = "bookmarks"
        serializer_class = BookmarkSerializer
        permission_classes = [is_owner("user")]
        exempt_from_registry = True

        def get_queryset(self):
            return self.model.objects.filter(target=post)

    factory = APIRequestFactory()

    def call(method, action, user):
        request = getattr(factory, method)(f"/bookmarks/{alice_bookmark.pk}/")
        request.user = user
        return BookmarkViewSet.as_view({method: action})(request, pk=alice_bookmark.pk)

    assert call("get", "retrieve", alice).status_code == 200
    assert call("get", "retrieve", bob).status_code == 403

    assert call("delete", "destroy", bob).status_code == 403
    assert Post.bookmarks.model.objects.filter(pk=alice_bookmark.pk).exists()

    assert call("delete", "destroy", alice).status_code == 204
    assert not Post.bookmarks.model.objects.filter(pk=alice_bookmark.pk).exists()
