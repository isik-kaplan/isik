import pytest
from rest_framework.test import APIRequestFactory

from isik.django.apps.feedback.comments.drf import generic_comment_serializer
from isik.django.drf.permissions import is_owner
from isik.django.drf.viewsets.base import BaseModelViewSet
from tests.testapp.models import Post


pytestmark = pytest.mark.django_db


# Built once at module scope - ModelSerializerRegistryMixin raises if the same model is
# registered twice, so calling the factory fresh in every test would collide on the second call.
CommentSerializer = generic_comment_serializer(Post.comments.model)


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
    comment = alice.comment(post, "nice post")
    fields = CommentSerializer().fields
    assert fields["body"].read_only is False
    assert fields["user"].read_only is True
    assert CommentSerializer(comment).data["body"] == "nice post"


def test_composes_with_is_owner_for_a_private_comment_viewset(alice, bob, post):
    alice_comment = alice.comment(post, "alice's comment")

    class CommentViewSet(BaseModelViewSet):
        model = Post.comments.model
        endpoint = "comments"
        serializer_class = CommentSerializer
        permission_classes = [is_owner("user")]

        def get_queryset(self):
            return self.model.objects.filter(target=post)

    factory = APIRequestFactory()

    def call(method, action, user, **kwargs):
        request = getattr(factory, method)(f"/comments/{alice_comment.pk}/", **kwargs)
        request.user = user
        return CommentViewSet.as_view({method: action})(request, pk=alice_comment.pk)

    assert call("get", "retrieve", alice).status_code == 200
    assert call("get", "retrieve", bob).status_code == 403

    response = call("patch", "partial_update", bob, data={"body": "hacked by bob"}, format="json")
    assert response.status_code == 403
    assert Post.comments.model.objects.get(pk=alice_comment.pk).body == "alice's comment"

    response = call("patch", "partial_update", alice, data={"body": "edited by alice"}, format="json")
    assert response.status_code == 200
    assert Post.comments.model.objects.get(pk=alice_comment.pk).body == "edited by alice"

    response = call("put", "update", bob, data={"body": "hacked by bob"}, format="json")
    assert response.status_code == 403

    response = call("delete", "destroy", bob)
    assert response.status_code == 403
    assert Post.comments.model.objects.filter(pk=alice_comment.pk).exists()

    response = call("delete", "destroy", alice)
    assert response.status_code == 204
    assert not Post.comments.model.objects.filter(pk=alice_comment.pk).exists()
