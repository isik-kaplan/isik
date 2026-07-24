from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.db.utils import IntegrityError
from django.test.utils import isolate_apps
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from isik.django.apps.common.db.models import BaseModel
from isik.django.apps.tags.tags import TaggableMixin, TagQuerySet, tags
from tests.testapp.models import Post


pytestmark = pytest.mark.django_db


def _create_tables(*models_):
    """isolate_apps models have no migration - create their tables for the duration of this
    test's transaction (rolled back automatically, like everything else under django_db)."""
    with connection.schema_editor() as editor:
        for model in models_:
            editor.create_model(model)


@pytest.fixture
def post():
    return Post.objects.create(title="hello")


def test_related_name_is_required():
    with pytest.raises(TypeError, match="related_name"):
        tags()


def test_post_has_two_independent_tag_pools():
    assert Post.topics.model is not Post.labels.model
    assert Post.topics.model._meta.get_field("name").unique is True


def test_add_tag_creates_and_attaches_a_tag(post):
    post.add_tag("python", field=Post.topics)
    assert post.tag_names(field=Post.topics) == ["python"]


def test_adding_the_same_name_twice_reuses_the_same_row(post):
    first = post.add_tag("python", field=Post.topics)
    second = post.add_tag("python", field=Post.topics)
    assert first.pk == second.pk
    assert Post.topics.model.objects.filter(name="python").count() == 1


def test_remove_tag_detaches_but_does_not_delete_the_tag_row(post):
    post.add_tag("python", field=Post.topics)
    post.remove_tag("python", field=Post.topics)
    assert post.tag_names(field=Post.topics) == []
    assert Post.topics.model.objects.filter(name="python").exists()


def test_deleting_the_host_cascades_the_through_row_but_not_the_tag(post):
    post.add_tag("python", field=Post.topics)
    tag = Post.topics.model.objects.get(name="python")
    post.delete()
    assert not Post.topics.through.objects.filter(tag=tag).exists()
    assert Post.topics.model.objects.filter(pk=tag.pk).exists()


def test_removing_a_tag_never_attached_is_a_no_op(post):
    post.remove_tag("never-added", field=Post.topics)
    assert post.tag_names(field=Post.topics) == []


def test_set_tags_replaces_the_whole_set(post):
    post.set_tags(["python", "django"], field=Post.topics)
    assert set(post.tag_names(field=Post.topics)) == {"python", "django"}
    post.set_tags(["rust"], field=Post.topics)
    assert post.tag_names(field=Post.topics) == ["rust"]


def test_set_tags_with_empty_list_detaches_everything(post):
    post.set_tags(["python", "django"], field=Post.topics)
    post.set_tags([], field=Post.topics)
    assert post.tag_names(field=Post.topics) == []


def test_post_topics_all_is_a_real_django_manager(post):
    post.add_tag("python", field=Post.topics)
    assert list(post.topics.all()) == list(Post.topics.model.objects.filter(name="python"))


def test_post_topics_through_is_native_to_the_m2m_descriptor():
    m2m_field = Post._meta.get_field("topics")
    assert Post.topics.through is m2m_field.remote_field.through
    assert Post.topics.through.__name__ == "PostTopicsObjectTag"


def test_related_name_reverse_accessor_reaches_back_to_the_hosts(post):
    other = Post.objects.create(title="second")
    tag = post.add_tag("python", field=Post.topics)
    other.add_tag("python", field=Post.topics)
    assert set(tag.posts_with_topic.all()) == {post, other}


class TestCreateRaceCondition:
    def test_create_recovers_from_a_duplicate_insert_racing_past_the_filter_check(self):
        # create()'s dedup check is filter-then-create, not an atomic get_or_create() - if two
        # callers both pass the filter before either insert lands, the second insert must be
        # reconciled against the `name` unique constraint instead of raising IntegrityError. Fakes
        # that race by making only the first filter() call (create()'s own dedup check) come back
        # empty; the recovery get() inside create() still uses the real filter().
        Tag = Post.topics.model
        existing = Tag.objects.create(name="python")
        original_filter = TagQuerySet.filter
        calls = []

        def fake_filter(self, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return Tag.objects.none()
            return original_filter(self, *args, **kwargs)

        with patch.object(TagQuerySet, "filter", fake_filter):
            tag = Tag.objects.create(name="python")
        assert tag.pk == existing.pk
        assert Tag.objects.filter(name="python").count() == 1


class TestMultipleAttachmentsOnTheSameHost:
    def test_two_tag_fields_have_entirely_separate_pools(self, post):
        post.add_tag("python", field=Post.topics)
        post.add_tag("python", field=Post.labels)
        assert Post.topics.model.objects.filter(name="python").count() == 1
        assert Post.labels.model.objects.filter(name="python").count() == 1
        assert Post.topics.model is not Post.labels.model

    def test_ambiguous_call_without_field_raises(self, post):
        with pytest.raises(TypeError, match="multiple taggable fields"):
            post.add_tag("python")

    @isolate_apps("tests.testapp")
    def test_reusing_target_related_name_across_two_tag_fields_on_the_same_host_raises(self):
        with pytest.raises(ValueError, match="already claimed"):

            class Host(models.Model):
                class Meta:
                    app_label = "testapp"

                topics = tags(related_name="host_topics")
                labels = tags(related_name="host_labels")


class TestBaseModelKwarg:
    @isolate_apps("tests.testapp")
    def test_tag_and_through_base_model_kwargs_are_applied_independently(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics", tag_base_model=BaseModel, through_base_model=BaseModel)

        assert issubclass(Host.topics.model, BaseModel)
        assert issubclass(Host.topics.through, BaseModel)


class TestNamingAndExtraFieldsKwargs:
    @isolate_apps("tests.testapp")
    def test_target_name_controls_the_through_models_host_fk_name(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics", target_name="host")

        field = Host.topics.through._meta.get_field("host")
        assert field.related_model is Host

    @isolate_apps("tests.testapp")
    def test_name_max_length_controls_the_generated_name_field(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics", name_max_length=20)

        assert Host.topics.model._meta.get_field("name").max_length == 20

    @isolate_apps("tests.testapp")
    def test_tag_extra_fields_are_added_to_the_generated_tag_model(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics", tag_extra_fields={"color": models.CharField(max_length=20)})

        _create_tables(Host.topics.model)
        tag = Host.topics.model.objects.create(name="python", color="green")
        assert tag.color == "green"

    @isolate_apps("tests.testapp")
    def test_through_extra_fields_are_added_to_the_generated_through_model(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(
                related_name="host_topics", through_extra_fields={"pinned": models.BooleanField(default=False)}
            )

        _create_tables(Host, Host.topics.model, Host.topics.through)
        host = Host.objects.create()
        tag = Host.topics.model.objects.get_tag("python")
        through = Host.topics.through.objects.create(tag=tag, target=host)
        assert through.pinned is False


class TestNormalize:
    @isolate_apps("tests.testapp")
    def test_without_normalize_different_cases_are_different_tags(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics")

        _create_tables(Host.topics.model)
        Host.topics.model.objects.get_tag("Python")
        Host.topics.model.objects.get_tag("python")
        assert Host.topics.model.objects.count() == 2

    @isolate_apps("tests.testapp")
    def test_with_normalize_different_cases_dedupe_to_one_tag(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics", normalize=str.lower)

        _create_tables(Host.topics.model)
        a = Host.topics.model.objects.get_tag("Python")
        b = Host.topics.model.objects.get_tag("python")
        assert a.pk == b.pk
        assert a.name == "python"
        assert Host.topics.model.objects.count() == 1


class TestNameValidation:
    def test_default_regex_rejects_disallowed_characters(self):
        tag = Post.topics.model(name="not valid!")
        with pytest.raises(ValidationError):
            tag.full_clean()

    def test_default_regex_accepts_letters_digits_dash_underscore_dot(self):
        tag = Post.topics.model(name="valid-Tag_1.0")
        tag.full_clean(exclude=[f.name for f in Post.topics.model._meta.get_fields() if f.name != "name"])

    @isolate_apps("tests.testapp")
    def test_name_validators_can_be_overridden(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics", name_validators=[])

        _create_tables(Host.topics.model)
        tag = Host.topics.model(name="anything at all!! 123")
        tag.full_clean(exclude=[f.name for f in Host.topics.model._meta.get_fields() if f.name != "name"])

    def test_get_tag_enforces_the_default_regex_too(self):
        # get_tag() is the choke point every write path funnels through (add_tag/set_tags/
        # TagManager.from_list/the DRF field) - create() bypasses full_clean(), so validation
        # has to be applied explicitly here rather than relying on the model layer.
        with pytest.raises(ValidationError):
            Post.topics.model.objects.get_tag("not valid!")

    def test_add_tag_with_an_invalid_name_raises(self, post):
        with pytest.raises(ValidationError):
            post.add_tag("not valid!", field=Post.topics)
        assert post.tag_names(field=Post.topics) == []

    def test_set_tags_with_an_invalid_name_raises(self, post):
        with pytest.raises(ValidationError):
            post.set_tags(["python", "not valid!"], field=Post.topics)

    @isolate_apps("tests.testapp")
    def test_get_tag_with_validators_overridden_to_empty_allows_any_characters(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics", name_validators=[])

        _create_tables(Host.topics.model)
        tag = Host.topics.model.objects.get_tag("anything at all!! 123")
        assert tag.name == "anything at all!! 123"


class TestFromList:
    def test_get_or_creates_every_name_and_returns_the_resulting_queryset(self, post):
        existing = Post.topics.model.objects.get_tag("python")
        result = Post.topics.model.objects.from_list(["python", "django"])
        assert set(result.values_list("name", flat=True)) == {"python", "django"}
        assert result.get(name="python").pk == existing.pk

    def test_an_invalid_name_in_the_list_raises(self):
        with pytest.raises(ValidationError):
            Post.topics.model.objects.from_list(["python", "not valid!"])


class TestUniqueConstraintOnThrough:
    def test_the_same_tag_cannot_be_attached_twice_at_the_db_level(self, post):
        tag = Post.topics.model.objects.get_tag("python")
        Post.topics.through.objects.create(tag=tag, target=post)
        with pytest.raises(IntegrityError):
            Post.topics.through.objects.create(tag=tag, target=post)


class TestTaggableMixinIsRequiredExplicitly:
    def test_a_plain_class_without_the_mixin_has_no_tag_verbs(self):
        assert not hasattr(object(), "add_tag")

    def test_mixing_it_in_adds_the_verbs(self):
        class Thing(TaggableMixin):
            pass

        assert hasattr(Thing(), "add_tag")
        assert hasattr(Thing(), "remove_tag")
        assert hasattr(Thing(), "set_tags")
        assert hasattr(Thing(), "tag_names")

    def test_tagging_something_never_attached_raises(self):
        class Thing(TaggableMixin):
            pass

        with pytest.raises(TypeError, match="not taggable"):
            Thing().add_tag("x")


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(names=st.lists(st.sampled_from(["python", "django", "rust", "go"]), max_size=10))
def test_set_tags_never_leaves_duplicate_rows_for_any_sequence_of_names(names, post):
    post.set_tags(names, field=Post.topics)
    stored = list(post.tag_names(field=Post.topics))
    assert len(stored) == len(set(stored))
    assert set(stored) == set(names)
