"""model= on votes()/comments()/notes()/bookmarks(): a model written by hand, checked against what each reads."""

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test.utils import isolate_apps

from isik.django.apps.common._model_makers import DefaultMakerBase
from isik.django.apps.feedback._attachment import config_of
from isik.django.apps.feedback.bookmarks import bookmarks
from isik.django.apps.feedback.comments import comments
from isik.django.apps.feedback.notes import notes
from isik.django.apps.feedback.votes import votes
from tests.testapp.models import Post, Thread, ThreadComment, ThreadVote


def host_with(attachment):
    class Host(models.Model):
        feedback = attachment

        class Meta:
            app_label = "testapp"

    return Host


def user_key(related_name="+"):
    return models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name=related_name)


class TestBinding:
    def test_the_hosts_accessor_carries_the_model_and_the_attachment(self):
        assert Thread.comments.model is ThreadComment
        assert Thread.comments.config.target_name == "thread"
        assert config_of(ThreadVote) is Thread.votes.config

    def test_a_model_not_bound_is_none_of_theirs(self):
        with pytest.raises(
            TypeError, match=r"^Thread isn't a model of votes\(\), comments\(\), notes\(\) or bookmarks\(\)$"
        ):
            config_of(Thread)

    @isolate_apps("tests.testapp")
    def test_a_model_defined_before_its_host(self):
        class Vote(DefaultMakerBase):
            host = models.ForeignKey("testapp.Host", on_delete=models.CASCADE, related_name="feedback")
            user = user_key()
            value = models.SmallIntegerField()

            class Meta:
                app_label = "testapp"
                unique_together = [("host", "user")]

        owner = host_with(votes(model=Vote))

        assert owner.feedback.model is Vote
        assert owner.feedback.config.target_name == "host"

    @isolate_apps("tests.testapp")
    def test_a_model_given_as_a_class(self):
        class Bookmark(DefaultMakerBase):
            user = user_key()

            class Meta:
                app_label = "testapp"

        Bookmark.add_to_class(
            "host", models.ForeignKey("testapp.Host", on_delete=models.CASCADE, related_name="feedback")
        )
        Bookmark._meta.unique_together = [("host", "user")]

        owner = host_with(bookmarks(model=Bookmark))

        assert owner.feedback.model is Bookmark

    @isolate_apps("tests.testapp")
    def test_target_name_picks_one_of_several_keys_to_the_host(self):
        owner = host_with(notes(model="testapp.Note", target_name="about"))

        class Note(DefaultMakerBase):
            about = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="feedback")
            also = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="+")
            user = user_key()
            body = models.TextField()

            class Meta:
                app_label = "testapp"

        assert owner.feedback.config.target_name == "about"

    def test_a_model_has_to_be_named_with_its_app(self):
        with pytest.raises(ValueError, match=r"^Invalid model reference 'Note'\. String model references must be"):
            host_with(notes(model="Note"))


class TestTheContract:
    @isolate_apps("tests.testapp")
    def test_every_problem_is_named_at_once(self):
        owner = host_with(votes(model="testapp.Vote"))

        with pytest.raises(ImproperlyConfigured) as raised:

            class Vote(models.Model):
                host = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="feedback")
                user = models.IntegerField()
                value = models.CharField(max_length=4)

                class Meta:
                    app_label = "testapp"

        assert str(raised.value) == (
            "testapp.Vote can't be votes()'s model: its user must be a ForeignKey to the user model; it has no "
            "created_at - votes() needs a DateTimeField named created_at; its value must be an IntegerField "
            "holding 1 or -1; it needs a unique constraint on (host, user) - votes() relies on it."
        )

    @isolate_apps("tests.testapp")
    def test_a_primary_key_named_otherwise_isnt_id(self):
        owner = host_with(bookmarks(model="testapp.Bookmark"))

        with pytest.raises(ImproperlyConfigured, match=r"its id must be a primary key named id"):

            class Bookmark(DefaultMakerBase):
                key = models.AutoField(primary_key=True)
                id = models.IntegerField()
                host = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="feedback")
                user = user_key()

                class Meta:
                    app_label = "testapp"
                    constraints = [models.UniqueConstraint(fields=["host", "user"], name="one_bookmark")]

    @isolate_apps("tests.testapp")
    def test_a_conditional_constraint_isnt_unique_enough(self):
        owner = host_with(bookmarks(model="testapp.Bookmark"))

        with pytest.raises(ImproperlyConfigured, match=r"it needs a unique constraint on \(host, user\)"):

            class Bookmark(DefaultMakerBase):
                host = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="feedback")
                user = user_key()

                class Meta:
                    app_label = "testapp"
                    constraints = [
                        models.UniqueConstraint(
                            fields=["host", "user"], condition=models.Q(id__gt=0), name="one_bookmark"
                        )
                    ]

    @isolate_apps("tests.testapp")
    def test_comments_and_notes_need_a_body_they_can_hold(self):
        owner = host_with(comments(model="testapp.Comment"))

        with pytest.raises(
            ImproperlyConfigured,
            match=r"^testapp\.Comment can't be comments\(\)'s model: its body must be a TextField, CharField or "
            r"JSONField; its parent must be a ForeignKey to the comment model itself; its edited_at must be a "
            r"DateTimeField\.$",
        ):

            class Comment(DefaultMakerBase):
                host = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="feedback")
                user = user_key()
                body = models.IntegerField()
                parent = models.ForeignKey(
                    settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE, related_name="+"
                )
                edited_at = models.BooleanField(default=False)

                class Meta:
                    app_label = "testapp"

    @isolate_apps("tests.testapp")
    def test_a_parent_every_comment_must_have_isnt_a_thread(self):
        owner = host_with(comments(model="testapp.Comment"))

        with pytest.raises(ImproperlyConfigured, match=r"its parent must be a ForeignKey to the comment model itself"):

            class Comment(DefaultMakerBase):
                host = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="feedback")
                user = user_key()
                body = models.TextField()
                parent = models.ForeignKey("self", on_delete=models.CASCADE, related_name="replies")

                class Meta:
                    app_label = "testapp"

    @isolate_apps("tests.testapp")
    def test_a_note_body_cant_be_a_document(self):
        owner = host_with(notes(model="testapp.Note"))

        with pytest.raises(ImproperlyConfigured, match=r"its body must be a TextField or CharField"):

            class Note(DefaultMakerBase):
                host = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="feedback")
                user = user_key()
                body = models.JSONField()

                class Meta:
                    app_label = "testapp"

    @isolate_apps("tests.testapp")
    def test_no_key_to_the_host(self):
        host_with(notes(model="testapp.Note"))

        with pytest.raises(
            ImproperlyConfigured,
            match=r"^testapp\.Note can't be notes\(\)'s model: it needs exactly one ForeignKey to testapp\.host, or "
            r"target_name= naming the one to use - it has 0\.$",
        ):

            class Note(DefaultMakerBase):
                user = user_key()
                body = models.TextField()

                class Meta:
                    app_label = "testapp"

    @isolate_apps("tests.testapp")
    def test_two_keys_to_the_host_without_a_target_name(self):
        owner = host_with(notes(model="testapp.Note"))

        with pytest.raises(ImproperlyConfigured, match=r"it has 2\.$"):

            class Note(DefaultMakerBase):
                one = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="feedback")
                two = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="+")
                user = user_key()
                body = models.TextField()

                class Meta:
                    app_label = "testapp"

    @pytest.mark.parametrize("target_name", ["user", "missing"])
    @isolate_apps("tests.testapp")
    def test_a_target_name_that_isnt_a_key_to_the_host(self, target_name):
        owner = host_with(notes(model="testapp.Note", target_name=target_name))

        with pytest.raises(
            ImproperlyConfigured,
            match=rf"^testapp\.Note can't be notes\(\)'s model: its {target_name} must be a ForeignKey to "
            r"testapp\.host\.$",
        ):

            class Note(DefaultMakerBase):
                host = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="feedback")
                user = user_key()
                body = models.TextField()

                class Meta:
                    app_label = "testapp"

    @isolate_apps("tests.testapp")
    def test_the_key_to_the_host_needs_an_accessor(self):
        owner = host_with(notes(model="testapp.Note"))

        with pytest.raises(
            ImproperlyConfigured,
            match=r"^testapp\.Note can't be notes\(\)'s model: its host needs a related_name that isn't hidden\.$",
        ):

            class Note(DefaultMakerBase):
                host = models.ForeignKey(owner, on_delete=models.CASCADE, related_name="+")
                user = user_key()
                body = models.TextField()

                class Meta:
                    app_label = "testapp"


class TestOptions:
    @pytest.mark.parametrize(
        ("maker", "options", "refused"),
        [
            (votes, {"user_related_name": "x", "model_name": "X"}, "model_name=, user_related_name="),
            (bookmarks, {"base_model": DefaultMakerBase}, "base_model="),
            (notes, {"body_max_length": 5, "extra_fields": {}}, "body_max_length=, extra_fields="),
            (comments, {"tiptap": True, "threaded": True}, "threaded=, tiptap="),
            (comments, {"track_edits": False, "comment_min_length": 2}, "comment_min_length=, track_edits="),
            (
                comments,
                {"comment_max_length": 5, "tiptap_schema_path": "x", "target_related_name": "x", "user_model": "x"},
                "comment_max_length=, target_related_name=, tiptap_schema_path=, user_model=",
            ),
        ],
    )
    def test_a_bound_model_is_used_as_it_is(self, maker, options, refused):
        name = maker.__name__
        with pytest.raises(TypeError, match=rf"^{name}\(\)\(model=\.\.\.\) uses the model as it is - drop {refused}$"):
            maker(model="testapp.Thing", **options)

    @pytest.mark.parametrize("maker", [votes, bookmarks, notes, comments])
    def test_a_built_model_needs_user_related_name(self, maker):
        with pytest.raises(TypeError, match=rf"^{maker.__name__}\(\) needs user_related_name= unless model= is given$"):
            maker()

    def test_max_depth_and_target_name_go_with_either(self):
        comments(model="testapp.Thing", max_depth=3, target_name="thing")


class TestBuiltModels:
    def test_the_defaults_are_as_before(self):
        config = Post.votes.config

        assert (config.target_name, config.target_related_name) == ("target", "votes")
        assert Post.comments.config.comment_min_length == 1
        assert Post.comments.model._meta.get_field("body").validators[0].limit_value == 1

    @isolate_apps("tests.testapp")
    def test_extra_fields_cant_replace_one_of_the_makers_own(self):
        with pytest.raises(
            TypeError,
            match=r"^extra_fields can't replace HostFeedbackVote's own user, value - give the extra field another "
            r"name$",
        ):
            host_with(
                votes(
                    user_related_name="host_votes",
                    extra_fields={"value": models.IntegerField(), "user": models.IntegerField()},
                )
            )

    def test_a_threaded_model_has_a_parent_and_an_edit_stamp(self):
        model = Post.comments.model

        assert model._meta.get_field("parent").remote_field.model is model
        assert model._meta.get_field("parent").null is True
        assert model._meta.get_field("edited_at").null is True
        assert (Post.comments.config.is_threaded, Post.comments.config.tracks_edits) == (True, True)

    def test_an_unthreaded_one_has_neither(self):
        from tests.testapp.models import Article

        assert {"parent", "edited_at"}.isdisjoint(field.name for field in Article.comments.model._meta.get_fields())
        assert (Article.comments.config.is_threaded, Article.comments.config.tracks_edits) == (False, False)
