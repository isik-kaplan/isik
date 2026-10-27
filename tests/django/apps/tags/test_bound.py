"""tags(tag_model=..., through_model=...) - a pool written by hand - and the usage counts of any pool."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test.utils import isolate_apps

from isik.django.apps.tags.drf import TagUsageField
from isik.django.apps.tags.tags import AbstractTag, TagManager, tags, with_tag_usage
from tests.testapp.models import Post, SharedTag, Thread, ThreadTopic


pytestmark = pytest.mark.django_db


def host_with(attachment):
    class Host(models.Model):
        topics = attachment

        class Meta:
            app_label = "testapp"

    return Host


class TestBound:
    def test_the_hosts_tags_are_the_pools_own(self):
        thread = Thread.objects.create(title="hello")

        thread.add_tag("python")
        thread.set_tags(["python", "django"])

        assert Thread.topics.model is SharedTag
        assert Thread.topics.through is ThreadTopic
        assert Thread.topics.config.target_name == "thread"
        assert sorted(thread.tag_names()) == ["django", "python"]
        assert SharedTag.objects.count() == 2

    def test_abstract_tag_validates_its_names(self):
        with pytest.raises(Exception, match="Tags can only contain"):
            SharedTag.objects.get_tag("not ok")

    def test_remove_tag_normalizes_as_add_tag_does(self, monkeypatch):
        monkeypatch.setattr(SharedTag, "_normalize", str.lower, raising=False)
        thread = Thread.objects.create(title="hello")
        thread.add_tag("Python")

        thread.remove_tag("PYTHON")

        assert thread.tag_names() == []

    def test_a_built_pool_normalizes_on_remove_too(self):
        post = Post.objects.create(title="hello")
        post.add_tag("python", field=Post.topics)

        post.remove_tag("python", field=Post.topics)

        assert post.tag_names(field=Post.topics) == []

    @isolate_apps("tests.testapp")
    def test_a_built_through_model_on_a_bound_tag_model(self):
        class Tag(AbstractTag):
            class Meta:
                app_label = "testapp"

        host = host_with(tags(related_name="hosts", tag_model=Tag))

        through = host.topics.through
        assert through._meta.get_field("tag").remote_field.model is Tag
        assert host.topics.config.target_name == "target"


class TestTheContract:
    @isolate_apps("tests.testapp")
    def test_a_tag_model_needs_a_unique_name(self):
        host_with(tags(related_name="hosts", tag_model="testapp.Tag"))

        with pytest.raises(
            ImproperlyConfigured,
            match=r"^testapp\.Tag can't be tags\(\)'s model: its name must be a unique CharField; it needs a unique "
            r"constraint on \(name\) - tags\(\) relies on it\.$",
        ):

            class Tag(models.Model):
                name = models.TextField()
                objects = TagManager()

                class Meta:
                    app_label = "testapp"

    @isolate_apps("tests.testapp")
    def test_a_tag_model_needs_the_tag_manager(self):
        host_with(tags(related_name="hosts", tag_model="testapp.Tag"))

        with pytest.raises(
            ImproperlyConfigured,
            match=r"^testapp\.Tag can't be tags\(\)'s model: its default manager must be a TagManager - see "
            r"AbstractTag\.$",
        ):

            class Tag(models.Model):
                name = models.CharField(max_length=10, unique=True)

                class Meta:
                    app_label = "testapp"

    @isolate_apps("tests.testapp")
    def test_a_through_model_needs_its_tag_key_and_its_constraint(self):
        class Tag(AbstractTag):
            class Meta:
                app_label = "testapp"

        owner = host_with(tags(related_name="hosts", tag_model=Tag, through_model="testapp.Link"))

        with pytest.raises(
            ImproperlyConfigured,
            match=r"^testapp\.Link can't be tags\(\)'s model: its tag must be a ForeignKey to the tag model; it "
            r"needs a unique constraint on \(tag, host\) - tags\(\) relies on it\.$",
        ):

            class Link(models.Model):
                tag = models.ForeignKey("auth.Group", on_delete=models.CASCADE, related_name="+")
                host = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="links")

                class Meta:
                    app_label = "testapp"

    def test_a_through_model_needs_a_tag_model(self):
        with pytest.raises(TypeError, match=r"^tags\(through_model=\.\.\.\) needs tag_model= too"):
            tags(related_name="x", through_model="testapp.Link")

    @pytest.mark.parametrize(
        ("options", "refused"),
        [
            (
                {"tag_model": "testapp.Tag", "normalize": str.lower, "name_max_length": 5},
                "tag_model=.*name_max_length=, normalize=",
            ),
            (
                {
                    "tag_model": "testapp.Tag",
                    "name_validators": [],
                    "tag_base_model": 1,
                    "tag_extra_fields": {},
                    "tag_model_name": "X",
                },
                "tag_model=.*name_validators=, tag_base_model=, tag_extra_fields=, tag_model_name=",
            ),
            (
                {
                    "tag_model": "testapp.Tag",
                    "through_model": "testapp.Link",
                    "target_related_name": "x",
                    "through_base_model": 1,
                    "through_extra_fields": {},
                    "through_model_name": "X",
                },
                "through_model=.*target_related_name=, through_base_model=, through_extra_fields=, through_model_name=",
            ),
        ],
    )
    def test_a_bound_model_is_used_as_it_is(self, options, refused):
        with pytest.raises(TypeError, match=rf"^tags\({refused}$"):
            tags(related_name="x", **options)


class TestUsage:
    def test_each_tag_counts_its_hosts_in_one_query(self, django_assert_num_queries):
        first, second = Post.objects.create(title="a"), Post.objects.create(title="b")
        first.add_tag("python", field=Post.topics)
        second.add_tag("python", field=Post.topics)
        second.add_tag("django", field=Post.topics)
        Post.topics.model.objects.get_tag("unused")

        with django_assert_num_queries(1):
            counts = dict(
                with_tag_usage(Post.topics.model.objects.all(), Post.topics).values_list("name", "usage_count")
            )

        assert counts == {"python": 2, "django": 1, "unused": 0}

    def test_a_shared_pool_counts_one_hosts_uses(self):
        thread = Thread.objects.create(title="a")
        thread.add_tag("python")

        assert with_tag_usage(SharedTag.objects.all(), Thread.topics).get().usage_count == 1

    def test_the_field_reads_the_annotation_or_counts_one_tag(self, django_assert_num_queries):
        post = Post.objects.create(title="a")
        post.add_tag("python", field=Post.topics)
        field = TagUsageField(Post.topics)
        annotated = with_tag_usage(Post.topics.model.objects.all(), Post.topics).get()

        with django_assert_num_queries(0):
            assert field.to_representation(annotated) == 1
        assert field.to_representation(Post.topics.model.objects.get()) == 1
        assert (field.source, field.read_only) == ("*", True)


class TestFreshModels:
    @isolate_apps("tests.testapp")
    def test_a_tag_model_named_by_its_label(self):
        owner = host_with(tags(related_name="hosts", tag_model="testapp.Tag"))

        class Tag(AbstractTag):
            class Meta:
                app_label = "testapp"

        assert owner.topics.model is Tag

    @isolate_apps("tests.testapp")
    def test_a_through_model_and_its_key_to_the_host(self):
        class Tag(AbstractTag):
            class Meta:
                app_label = "testapp"

        owner = host_with(tags(related_name="hosts", tag_model=Tag, through_model="testapp.Link"))

        class Link(models.Model):
            tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
            about = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="links")

            class Meta:
                app_label = "testapp"
                constraints = [models.UniqueConstraint(fields=["about", "tag"], name="one_link")]

        assert owner.topics.through is Link
        assert owner.topics.config.target_name == "about"

    @isolate_apps("tests.testapp")
    def test_target_name_picks_the_through_models_key(self):
        class Tag(AbstractTag):
            class Meta:
                app_label = "testapp"

        owner = host_with(tags(related_name="hosts", tag_model=Tag, through_model="testapp.Link", target_name="about"))

        class Link(models.Model):
            tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
            about = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="links")
            also = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="+")

            class Meta:
                app_label = "testapp"
                unique_together = [("tag", "about")]

        assert owner.topics.config.target_name == "about"

    @isolate_apps("tests.testapp")
    def test_a_through_model_without_a_key_to_the_host(self):
        class Tag(AbstractTag):
            class Meta:
                app_label = "testapp"

        host_with(tags(related_name="hosts", tag_model=Tag, through_model="testapp.Link"))

        with pytest.raises(
            ImproperlyConfigured,
            match=r"^testapp\.Link can't be tags\(\)'s model: it needs exactly one ForeignKey to testapp\.host, or "
            r"target_name= naming the one to use - it has 0\.$",
        ):

            class Link(models.Model):
                tag = models.ForeignKey(Tag, on_delete=models.CASCADE)

                class Meta:
                    app_label = "testapp"

    @pytest.mark.parametrize(
        ("options", "model"),
        [
            ({"tag_extra_fields": {"name": models.TextField()}}, "Tag"),
            ({"through_extra_fields": {"tag": models.TextField()}}, "ObjectTag"),
        ],
    )
    @isolate_apps("tests.testapp")
    def test_extra_fields_cant_replace_the_makers_own(self, options, model):
        with pytest.raises(TypeError, match=rf"^extra_fields can't replace HostTopics{model}'s own \w+ - give"):
            host_with(tags(related_name="hosts", **options))

    def test_the_usage_fields_copy_keeps_its_own_options(self):
        import copy

        copied = copy.deepcopy(TagUsageField(Post.topics, help_text="its uses"))

        assert (copied.tagged, copied.help_text) == (Post.topics, "its uses")
