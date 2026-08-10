import pytest
from django.db import connection, models
from django.test.utils import isolate_apps
from rest_framework import serializers

from isik.django.apps.tags.drf import generic_tag_field, generic_tag_serializer
from isik.django.apps.tags.tags import tags
from isik.django.drf.serializers.base import BaseModelSerializer
from isik.django.drf.utils.related_count import ModelRelatedCountField
from tests.testapp.models import Post


pytestmark = pytest.mark.django_db


def _fresh_host_with_tags(**tags_kwargs):
    """A brand new isolate_apps host+tags() attachment - unlike Post.topics (module-level, frozen
    at first import), this actually re-executes generic_tag_field()/generic_tag_serializer() fresh
    every call, so it's what catches a mutation to either under a tool that reruns pytest in the
    same process (e.g. mutation testing) instead of just replaying whatever ran the first time."""

    class Host(models.Model):
        class Meta:
            app_label = "testapp"

        topics = tags(related_name="host_topics", **tags_kwargs)

    return Host


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


@isolate_apps("tests.testapp")
def test_tag_list_field_child_max_length_matches_the_real_tag_models_name_field():
    Host = _fresh_host_with_tags(name_max_length=17)
    field = generic_tag_field(Host.topics)
    assert field.child.max_length == 17


@isolate_apps("tests.testapp")
def test_tag_list_field_rejects_a_name_over_max_length_before_touching_the_db():
    Host = _fresh_host_with_tags(name_max_length=5)
    field = generic_tag_field(Host.topics)
    with pytest.raises(serializers.ValidationError):
        field.child.run_validation("toolong")


@isolate_apps("tests.testapp")
def test_tag_list_field_to_representation_reads_the_manager_as_a_plain_list():
    class FakeManager:
        def to_list(self):
            return ["a", "b"]

    Host = _fresh_host_with_tags()
    field = generic_tag_field(Host.topics)
    assert field.to_representation(FakeManager()) == ["a", "b"]


@isolate_apps("tests.testapp")
def test_generic_tag_serializer_meta_model_and_fields():
    Host = _fresh_host_with_tags()
    Serializer = generic_tag_serializer(Host.topics)
    assert Serializer.Meta.model is Host.topics.model
    assert Serializer.Meta.fields == ["id", "name", "usage_count"]


@isolate_apps("tests.testapp")
def test_generic_tag_serializer_usage_count_field_is_present_and_wired():
    Host = _fresh_host_with_tags()
    Serializer = generic_tag_serializer(Host.topics)
    field = Serializer().fields["usage_count"]
    assert isinstance(field, ModelRelatedCountField)
    assert field.related_name == Host.topics.config.related_name


@isolate_apps("tests.testapp")
def test_tag_list_field_run_validation_dedupes_via_the_real_tag_model():
    Host = _fresh_host_with_tags()
    with connection.schema_editor() as editor:
        editor.create_model(Host.topics.model)
    existing = Host.topics.model.objects.create(name="python")

    field = generic_tag_field(Host.topics)
    tag = field.child.run_validation("python")

    assert tag.pk == existing.pk


@isolate_apps("tests.testapp")
def test_generic_tag_serializer_class_name_is_derived_from_the_tag_model():
    Host = _fresh_host_with_tags()
    Serializer = generic_tag_serializer(Host.topics)
    assert Serializer.__name__ == f"{Host.topics.model.__name__}Serializer"
