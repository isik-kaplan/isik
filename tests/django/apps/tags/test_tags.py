import uuid
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.db.utils import IntegrityError
from django.test import override_settings
from django.test.utils import isolate_apps
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from isik.django.apps.common._model_makers import claim_related_name, resolve_field
from isik.django.apps.common.db.models import BaseModel
from isik.django.apps.tags.tags import TAG_NAME_REGEX_ERROR_MESSAGE, TaggableMixin, TagQuerySet, _TagsField, tags
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


class TestCreateDedup:
    def test_create_reuses_an_existing_row_via_the_filter_check_alone(self, django_assert_num_queries):
        # If the filter-then-create dedup check filtered on the wrong value, it would never find
        # the existing row up front - it would fall through to super().create(), hit the `name`
        # unique constraint, and only recover via the except IntegrityError branch's own get().
        # Both paths return the same row, so only the query count tells them apart.
        Tag = Post.topics.model
        Tag.objects.create(name="python")
        with django_assert_num_queries(1):
            Tag.objects.create(name="python")


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


class TestDefaults:
    @isolate_apps("tests.testapp")
    def test_related_name_reaches_the_m2m_field(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics")

        assert Host._meta.get_field("topics").remote_field.related_name == "host_topics"

    @isolate_apps("tests.testapp")
    def test_target_name_defaults_to_target(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics")

        field = Host.topics.through._meta.get_field("target")
        assert field.related_model is Host

    @isolate_apps("tests.testapp")
    def test_target_related_name_defaults_to_tags(self):
        # A uuid-suffixed class name, not a plain "Host" - claim_related_name()'s registry is
        # keyed by app_label+class name, process-global, and never cleared between tests. A fixed
        # name would let a stale claim of "tags" left behind by an *earlier* mutant's run of this
        # same test (under a mutation testing tool that re-invokes pytest in the same process)
        # linger and mask whether *this* run's own target_related_name default was ever reached.
        Host = type(
            f"Host{uuid.uuid4().hex}",
            (models.Model,),
            {
                "__module__": __name__,
                "Meta": type("Meta", (), {"app_label": "testapp"}),
                "topics": tags(related_name="host_topics"),
            },
        )

        with pytest.raises(ValueError, match="already claimed"):
            claim_related_name(Host, "tags", "somebody-else")

    @isolate_apps("tests.testapp")
    def test_name_max_length_defaults_to_100(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics")

        assert Host.topics.model._meta.get_field("name").max_length == 100


def _host_model(**tags_kwargs):
    """A fresh, uniquely-named Host - BaseModel now carries pgtrigger triggers itself (see
    isik/django/apps/common/db/models.py), and pgtrigger's registry is process-global and keyed
    by db_table/trigger name, so a fixed "Host" class name would collide with itself on a second
    in-process run of a test using it (e.g. a mutation-testing tool re-invoking pytest without
    restarting) - the same reason tests/django/apps/common/db/test_history.py uuid-suffixes its
    own throwaway tracked models."""
    return type(
        f"Host{uuid.uuid4().hex[:8]}",
        (models.Model,),
        {
            "__module__": __name__,
            "Meta": type("Meta", (), {"app_label": "testapp"}),
            "topics": tags(related_name="host_topics", **tags_kwargs),
        },
    )


class TestBaseModelKwarg:
    @isolate_apps("tests.testapp")
    def test_tag_and_through_base_model_kwargs_are_applied_independently(self):
        Host = _host_model(tag_base_model=BaseModel, through_base_model=BaseModel)

        assert issubclass(Host.topics.model, BaseModel)
        assert issubclass(Host.topics.through, BaseModel)

    @isolate_apps("tests.testapp")
    def test_tag_base_model_falls_back_to_the_tags_tag_base_model_setting(self):
        with override_settings(TAGS_TAG_BASE_MODEL="isik.django.apps.common.db.models.BaseModel"):
            Host = _host_model()

        assert issubclass(Host.topics.model, BaseModel)

    @isolate_apps("tests.testapp")
    def test_through_base_model_falls_back_to_the_tags_through_base_model_setting(self):
        with override_settings(TAGS_THROUGH_BASE_MODEL="isik.django.apps.common.db.models.BaseModel"):
            Host = _host_model()

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
    def test_target_related_name_wires_the_through_models_host_fk_reverse_accessor(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics", target_related_name="my_tag_links")

        field = Host.topics.through._meta.get_field("target")
        assert field.remote_field.related_name == "my_tag_links"

    @isolate_apps("tests.testapp")
    def test_m2m_field_is_blank_true(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics")

        assert Host._meta.get_field("topics").blank is True

    @isolate_apps("tests.testapp")
    def test_config_is_wired_to_the_tags_field_instance_not_none(self):
        # A fresh Host, not Post - Post.topics.config was built once at module-import time and
        # would never observe a broken config=self here even under a normal test run.
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics")
            labels = tags(related_name="host_labels", target_related_name="host_labels_tags")

        with pytest.raises(TypeError, match="multiple taggable fields"):
            resolve_field(Host(), None, _TagsField, "taggable")

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
        with pytest.raises(ValidationError) as exc_info:
            tag.full_clean()
        assert exc_info.value.message_dict["name"] == [TAG_NAME_REGEX_ERROR_MESSAGE]

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

    @isolate_apps("tests.testapp")
    def test_default_regex_and_message_are_built_fresh_not_read_off_a_module_level_model(self):
        # Post.topics's validators were built once at module import time - a mutation to how
        # _TagsField.__init__ builds the default RegexValidator wouldn't be observable through a
        # model that was already constructed before the mutation was selected. A fresh Host built
        # here, inside the test body, always re-runs that construction under the active mutation.
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics")

        _create_tables(Host.topics.model)
        tag = Host.topics.model(name="not valid!")
        with pytest.raises(ValidationError) as exc_info:
            tag.full_clean()
        assert exc_info.value.message_dict["name"] == [TAG_NAME_REGEX_ERROR_MESSAGE]

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

    @isolate_apps("tests.testapp")
    def test_freshly_built_through_model_enforces_the_constraint_too(self):
        # Post.topics.through's constraint is baked into an already-applied migration, so it'd
        # enforce uniqueness at the DB level even if contribute_to_class stopped building one -
        # a fresh Host here has its table created from the live model definition instead.
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics")

        _create_tables(Host, Host.topics.model, Host.topics.through)
        host = Host.objects.create()
        tag = Host.topics.model.objects.get_tag("python")
        Host.topics.through.objects.create(tag=tag, target=host)
        with pytest.raises(IntegrityError):
            Host.topics.through.objects.create(tag=tag, target=host)

    @isolate_apps("tests.testapp")
    def test_constraint_name_is_lowercased(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics")

        (constraint,) = Host.topics.through._meta.constraints
        assert constraint.name == "unique_hosttopicsobjecttag"

    @isolate_apps("tests.testapp")
    def test_the_tag_models_own_name_field_is_unique_freshly_built(self):
        # Post.topics's name field was built once at module import time - a mutation to how
        # contribute_to_class() builds it (unique=True dropped/flipped) wouldn't be observable
        # through a model already constructed before the mutation was selected.
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

            topics = tags(related_name="host_topics")

        field = Host.topics.model._meta.get_field("name")
        assert field.unique is True
        assert field.max_length == 100
        # The validators kwarg itself (dropped vs. kept) is covered behaviorally by
        # test_default_regex_and_message_are_built_fresh_not_read_off_a_module_level_model above -
        # field.validators is non-empty either way once max_length auto-adds its own.


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

        with pytest.raises(TypeError, match="^Thing is not taggable"):
            Thing().add_tag("x")

    def test_removing_from_something_never_attached_raises(self):
        class Thing(TaggableMixin):
            pass

        with pytest.raises(TypeError, match="^Thing is not taggable"):
            Thing().remove_tag("x")

    def test_setting_tags_on_something_never_attached_raises(self):
        class Thing(TaggableMixin):
            pass

        with pytest.raises(TypeError, match="^Thing is not taggable"):
            Thing().set_tags(["x"])

    def test_tag_names_on_something_never_attached_raises(self):
        class Thing(TaggableMixin):
            pass

        with pytest.raises(TypeError, match="^Thing is not taggable"):
            Thing().tag_names()


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(names=st.lists(st.sampled_from(["python", "django", "rust", "go"]), max_size=10))
def test_set_tags_never_leaves_duplicate_rows_for_any_sequence_of_names(names, post):
    post.set_tags(names, field=Post.topics)
    stored = list(post.tag_names(field=Post.topics))
    assert len(stored) == len(set(stored))
    assert set(stored) == set(names)
