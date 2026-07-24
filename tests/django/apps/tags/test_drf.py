import pytest

from isik.django.apps.tags.drf import generic_tag_field, generic_tag_serializer
from isik.django.drf.serializers.base import BaseModelSerializer
from tests.testapp.models import Post


pytestmark = pytest.mark.django_db


@pytest.fixture
def post():
    return Post.objects.create(title="hello")


# Built once at module scope, like real usage - ModelSerializerRegistryMixin raises if the same
# model is registered twice, so calling these factories fresh in every test would collide.
class PostSerializer(BaseModelSerializer):
    topics = generic_tag_field(Post.topics)

    class Meta:
        model = Post
        fields = ["id", "title", "topics"]


TagSerializer = generic_tag_serializer(Post.topics)


def test_reading_a_host_returns_a_plain_list_of_tag_names(post):
    post.add_tag("python", field=Post.topics)
    post.add_tag("django", field=Post.topics)
    data = PostSerializer(post).data
    assert set(data["topics"]) == {"python", "django"}


def test_writing_a_host_dedupes_and_attaches_tags(post):
    serializer = PostSerializer(post, data={"title": post.title, "topics": ["python", "python", "django"]})
    assert serializer.is_valid(), serializer.errors
    serializer.save()

    assert set(post.tag_names(field=Post.topics)) == {"python", "django"}
    assert Post.topics.model.objects.filter(name="python").count() == 1


def test_writing_an_invalid_tag_name_fails_serializer_validation(post):
    serializer = PostSerializer(post, data={"title": post.title, "topics": ["not valid!"]})
    assert not serializer.is_valid()
    assert "topics" in serializer.errors
    assert post.tag_names(field=Post.topics) == []


def test_writing_an_empty_list_clears_all_tags(post):
    post.add_tag("python", field=Post.topics)
    serializer = PostSerializer(post, data={"title": post.title, "topics": []})
    assert serializer.is_valid(), serializer.errors
    serializer.save()
    assert post.tag_names(field=Post.topics) == []


def test_writing_a_tag_name_over_max_length_fails_validation_before_get_tag_runs(post):
    max_length = Post.topics.model._meta.get_field("name").max_length
    too_long = "a" * (max_length + 1)
    serializer = PostSerializer(post, data={"title": post.title, "topics": [too_long]})
    assert not serializer.is_valid()
    assert "topics" in serializer.errors
    assert post.tag_names(field=Post.topics) == []


def test_writing_reuses_an_existing_tag_row_instead_of_duplicating(post):
    existing = Post.topics.model.objects.get_tag("python")
    serializer = PostSerializer(post, data={"title": post.title, "topics": ["python"]})
    assert serializer.is_valid(), serializer.errors
    serializer.save()

    assert Post.topics.model.objects.get(name="python").pk == existing.pk


def test_generic_tag_serializer_exposes_id_name_and_usage_count(post):
    other = Post.objects.create(title="second")
    post.add_tag("python", field=Post.topics)
    other.add_tag("python", field=Post.topics)

    tag = Post.topics.model.objects.get(name="python")
    data = TagSerializer(tag).data

    assert data["name"] == "python"
    assert data["usage_count"] == 2


def test_generic_tag_serializer_usage_count_is_read_only():
    assert TagSerializer().fields["usage_count"].read_only is True
