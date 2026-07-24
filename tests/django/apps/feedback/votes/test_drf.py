import pytest
from rest_framework.test import APIRequestFactory

from isik.django.apps.feedback.votes.drf import generic_vote_serializer
from isik.django.drf.permissions import is_owner
from isik.django.drf.viewsets.base import BaseModelViewSet
from tests.testapp.models import Post


pytestmark = pytest.mark.django_db


# Built once at module scope, like real usage (`VoteSerializer = generic_vote_serializer(...)`) -
# ModelSerializerRegistryMixin raises if the same model is registered twice, so calling the
# factory fresh in every test would collide on the second call.
VoteSerializer = generic_vote_serializer(Post.votes.model)


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
    vote = Post.votes.model.objects.create(target=post, user=alice, value=1)
    data = VoteSerializer(vote).data
    assert data["user"] == alice.pk
    assert "user" in VoteSerializer().fields
    assert VoteSerializer().fields["user"].read_only is True


def test_value_is_writable(alice, post):
    assert VoteSerializer().fields["value"].read_only is False


def test_a_value_outside_the_choices_is_rejected_by_the_serializer():
    serializer = VoteSerializer(data={"value": 5})
    assert not serializer.is_valid()
    assert "value" in serializer.errors


def test_composes_with_is_owner_for_a_private_vote_viewset(alice, bob, post):
    alice.upvote(post)
    alice_vote = Post.votes.model.objects.get(target=post, user=alice)

    class VoteViewSet(BaseModelViewSet):
        model = Post.votes.model
        endpoint = "votes"
        serializer_class = VoteSerializer
        permission_classes = [is_owner("user")]

        def get_queryset(self):
            return self.model.objects.filter(target=post)

    factory = APIRequestFactory()

    def call(method, action, user, **kwargs):
        request = getattr(factory, method)(f"/votes/{alice_vote.pk}/", **kwargs)
        request.user = user
        return VoteViewSet.as_view({method: action})(request, pk=alice_vote.pk)

    assert call("get", "retrieve", alice).status_code == 200
    assert call("get", "retrieve", bob).status_code == 403

    response = call("patch", "partial_update", bob, data={"value": -1}, format="json")
    assert response.status_code == 403
    assert Post.votes.model.objects.get(pk=alice_vote.pk).value == 1

    response = call("patch", "partial_update", alice, data={"value": -1}, format="json")
    assert response.status_code == 200
    assert Post.votes.model.objects.get(pk=alice_vote.pk).value == -1

    assert call("delete", "destroy", bob).status_code == 403
    assert Post.votes.model.objects.filter(pk=alice_vote.pk).exists()

    assert call("delete", "destroy", alice).status_code == 204
    assert not Post.votes.model.objects.filter(pk=alice_vote.pk).exists()
