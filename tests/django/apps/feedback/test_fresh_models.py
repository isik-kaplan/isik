"""Each maker building its model, and binding one, at test time - what happens as a host class is made."""

import copy
import uuid

import pytest
from django.conf import settings
from django.db import models
from django.db.models.signals import class_prepared
from django.test.utils import isolate_apps
from django.utils.functional import Promise

from isik.django.apps.common import _model_makers
from isik.django.apps.common._model_makers import DefaultMakerBase
from isik.django.apps.feedback._attachment import config_of
from isik.django.apps.feedback.bookmarks import bookmarks
from isik.django.apps.feedback.comments import comments
from isik.django.apps.feedback.drf import VotesField
from isik.django.apps.feedback.notes import notes
from isik.django.apps.feedback.votes import votes
from tests.testapp.models import EmailUser, Post


def fresh_host(**attachments):
    return type(
        f"Host{uuid.uuid4().hex[:8]}",
        (models.Model,),
        {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"}), **attachments},
    )


def related():
    return f"made_{uuid.uuid4().hex[:8]}"


@pytest.mark.parametrize(
    ("maker", "target_text", "user_text"),
    [
        (votes, "The {host} this vote is on.", "Who cast this vote."),
        (bookmarks, "The {host} bookmarked.", "Who bookmarked it."),
        (notes, "The {host} this note is on.", "Who wrote this note."),
        (comments, "The {host} this comment is on.", "Who wrote this comment."),
    ],
)
@isolate_apps("tests.testapp")
def test_a_built_models_keys_say_what_they_hold_in_any_language(maker, target_text, user_text):
    host = fresh_host(feedback=maker(user_related_name=related(), user_model=EmailUser, target_name="item"))
    model = host.feedback.model
    item, user = model._meta.get_field("item"), model._meta.get_field("user")

    assert host.feedback.config.host is host
    assert config_of(model) is host.feedback.config
    assert isinstance(item.help_text, Promise)
    assert isinstance(user.help_text, Promise)
    assert (str(item.help_text), item.db_comment) == (target_text.format(host=host.__name__),) * 2
    assert (str(user.help_text), user.db_comment) == (user_text, user_text)


@isolate_apps("tests.testapp")
def test_a_bound_model_is_the_attachments():
    host = fresh_host(feedback=votes(model="testapp.Made"))

    class Made(DefaultMakerBase):
        on = models.ForeignKey(host, on_delete=models.CASCADE, related_name="feedback")
        user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
        value = models.SmallIntegerField()

        class Meta:
            app_label = "testapp"
            constraints = [models.UniqueConstraint(fields=["user", "on"], name="made_once")]

    assert config_of(Made) is host.feedback.config
    assert host.feedback.config.host is host
    assert host.feedback.config.target_name == "on"


@isolate_apps("tests.testapp")
def test_a_bound_comment_model_threads_by_its_parent():
    host = fresh_host(feedback=comments(model="testapp.Made"))

    class Made(DefaultMakerBase):
        on = models.ForeignKey(host, on_delete=models.CASCADE, related_name="feedback")
        user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
        body = models.TextField()
        parent = models.ForeignKey("self", null=True, on_delete=models.CASCADE, related_name="replies")

        class Meta:
            app_label = "testapp"

    assert host.feedback.config.is_threaded is True
    assert host.feedback.config.tracks_edits is False


@isolate_apps("tests.testapp")
def test_a_bound_comment_model_is_checked_past_a_missing_optional_field():
    from django.core.exceptions import ImproperlyConfigured

    host = fresh_host(feedback=comments(model="testapp.Made"))

    with pytest.raises(ImproperlyConfigured, match=r": its edited_at must be a DateTimeField\.$"):

        class Made(DefaultMakerBase):
            on = models.ForeignKey(host, on_delete=models.CASCADE, related_name="feedback")
            user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
            body = models.TextField()
            edited_at = models.TextField()

            class Meta:
                app_label = "testapp"


@isolate_apps("tests.testapp")
def test_waiting_for_a_model_stops_once_its_heard():
    listening = len(class_prepared.receivers)
    host = fresh_host(feedback=notes(model="testapp.Made"))

    assert len(_model_makers._waiting) == 1
    assert len(class_prepared.receivers) == listening + 1

    class Made(DefaultMakerBase):
        on = models.ForeignKey(host, on_delete=models.CASCADE, related_name="feedback")
        user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
        body = models.TextField()

        class Meta:
            app_label = "testapp"

    assert _model_makers._waiting == set()
    assert len(class_prepared.receivers) == listening
    assert host.feedback.model is Made


@isolate_apps("tests.testapp")
def test_another_model_prepared_meanwhile_isnt_the_one_waited_for():
    host = fresh_host(feedback=notes(model="testapp.Made"))

    class Other(models.Model):
        class Meta:
            app_label = "testapp"

    assert len(_model_makers._waiting) == 1
    assert not hasattr(host, "feedback")

    class Made(DefaultMakerBase):
        on = models.ForeignKey(host, on_delete=models.CASCADE, related_name="feedback")
        user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
        body = models.TextField()

        class Meta:
            app_label = "testapp"

    assert host.feedback.model is Made


def test_a_fields_copy_keeps_its_own_options():
    field = VotesField(Post.votes, help_text="its votes")

    copied = copy.deepcopy(field)

    assert (copied.field, copied.help_text) == (Post.votes, "its votes")
