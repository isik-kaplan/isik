import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.db.utils import DataError
from django.test import override_settings
from django.test.utils import isolate_apps

from isik.django.apps.common.db.models import BaseModel
from isik.django.apps.feedback.notes import notes
from isik.django.apps.feedback.votes import votes
from tests.testapp.models import Article, EmailUser, Post


pytestmark = pytest.mark.django_db


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="x")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user(username="bob", email="bob@example.com", password="x")


@pytest.fixture
def post():
    return Post.objects.create(title="hello")


def test_user_related_name_is_required():
    with pytest.raises(TypeError, match="user_related_name"):
        notes()


def test_add_note_creates_a_row(alice, post):
    note = alice.add_note(post, "remember to follow up")
    assert note.body == "remember to follow up"
    assert note.user == alice
    assert getattr(note, Post.notes.config.target_name) == post


def test_multiple_notes_from_the_same_user_are_all_kept_no_unique_constraint(alice, post):
    alice.add_note(post, "first")
    alice.add_note(post, "second")
    assert Post.notes.model.objects.filter(target=post, user=alice).count() == 2


def test_notes_on_returns_only_this_users_notes(alice, bob, post):
    alice.add_note(post, "alice's note")
    bob.add_note(post, "bob's note")
    assert list(alice.notes_on(post).values_list("body", flat=True)) == ["alice's note"]


def test_each_note_is_independently_editable(alice, post):
    note = alice.add_note(post, "first draft")
    note.body = "edited"
    note.save()
    assert Post.notes.model.objects.get(pk=note.pk).body == "edited"


def test_each_note_is_independently_deletable(alice, post):
    first = alice.add_note(post, "keep")
    second = alice.add_note(post, "delete me")
    second.delete()
    remaining = list(Post.notes.model.objects.filter(target=post, user=alice))
    assert remaining == [first]


def test_noting_something_never_attached_raises(alice):
    article = Article.objects.create(title="a")
    with pytest.raises(TypeError, match="not noteable"):
        alice.add_note(article, "x")


def test_base_model_kwarg_lets_the_generated_model_inherit_a_custom_base():
    assert issubclass(Post.notes.model, BaseModel)


class TestBaseModelResolution:
    @isolate_apps("tests.testapp")
    def test_explicit_base_model_kwarg_lets_the_generated_note_model_inherit_a_custom_base(self):
        class ExplicitNoteBase(models.Model):
            class Meta:
                app_label = "testapp"
                abstract = True

        class ExplicitBaseNoteHost(models.Model):
            class Meta:
                app_label = "testapp"

            notes = notes(user_related_name="explicit_base_note_host_notes", base_model=ExplicitNoteBase)

        assert issubclass(ExplicitBaseNoteHost.notes.model, ExplicitNoteBase)

    @isolate_apps("tests.testapp")
    def test_feedback_notes_base_model_setting_is_used_when_no_explicit_base_model_is_given(self):
        with override_settings(FEEDBACK_NOTES_BASE_MODEL="isik.django.apps.common.db.models.BaseModel"):
            from isik.django.apps.common.db.models import BaseModel

            class SettingBaseNoteHost(models.Model):
                class Meta:
                    app_label = "testapp"

                notes = notes(user_related_name="setting_base_note_host_notes")

            assert issubclass(SettingBaseNoteHost.notes.model, BaseModel)

    @isolate_apps("tests.testapp")
    def test_a_non_abstract_base_model_raises_type_error(self):
        from tests.testapp.models import Widget

        with pytest.raises(TypeError, match="abstract"):

            class NonAbstractBaseNoteHost(models.Model):
                class Meta:
                    app_label = "testapp"

                notes = notes(user_related_name="non_abstract_base_note_host_notes", base_model=Widget)


class TestCascadeDelete:
    def test_deleting_the_host_cascades_to_its_notes(self, alice, post):
        alice.add_note(post, "a note")
        post.delete()
        assert not Post.notes.model.objects.filter(user=alice).exists()

    def test_deleting_the_user_cascades_to_their_notes(self, alice, post):
        alice.add_note(post, "a note")
        alice.delete()
        assert not Post.notes.model.objects.filter(target=post).exists()


def _create_tables(*models_):
    """isolate_apps models have no migration - create their tables for the duration of this
    test's transaction (rolled back automatically, like everything else under django_db)."""
    with connection.schema_editor() as editor:
        for model in models_:
            editor.create_model(model)


@isolate_apps("tests.testapp")
def test_reusing_a_user_related_name_across_two_noteable_models_raises():
    """Two different noteable host models silently sharing a user_related_name on the User model
    would otherwise clash."""

    class HostA(models.Model):
        class Meta:
            app_label = "testapp"

        notes = notes(user_related_name="shared_note_name")

    with pytest.raises(ValueError, match="already claimed"):

        class HostB(models.Model):
            class Meta:
                app_label = "testapp"

            notes = notes(user_related_name="shared_note_name")


@isolate_apps("tests.testapp")
def test_reusing_a_target_related_name_on_the_same_host_raises():
    """Two notes() attachments on one host silently sharing a target_related_name would clash on
    the host's own reverse-accessor namespace."""
    with pytest.raises(ValueError, match="already claimed"):

        class HostWithCollidingNoteFields(models.Model):
            class Meta:
                app_label = "testapp"

            first = notes(user_related_name="colliding_note_fields_first", target_related_name="shared")
            second = notes(user_related_name="colliding_note_fields_second", target_related_name="shared")


@isolate_apps("tests.testapp")
def test_extra_fields_can_attach_a_maker_to_the_generated_note_model(alice):
    """user_model=EmailUser is explicit rather than the default AUTH_USER_MODEL dotted string:
    isolate_apps registers a fresh, isolated Apps() with only what's defined in this block, and a
    lazy "testapp.EmailUser" string can't resolve against it once real tables are created below."""

    class NoteExtraFieldsHost(models.Model):
        class Meta:
            app_label = "testapp"

        notes = notes(
            user_related_name="note_extra_fields_host_notes",
            user_model=EmailUser,
            extra_fields={"votes": votes(user_related_name="note_extra_fields_host_note_votes", user_model=EmailUser)},
        )

    _create_tables(NoteExtraFieldsHost, NoteExtraFieldsHost.notes.model, NoteExtraFieldsHost.notes.model.votes.model)

    host = NoteExtraFieldsHost.objects.create()
    note = alice.add_note(host, "a note")

    assert hasattr(NoteExtraFieldsHost.notes.model, "votes")
    alice.upvote(note)
    assert NoteExtraFieldsHost.notes.model.votes.model.objects.filter(target=note, user=alice, value=1).exists()


class TestBodyMaxLength:
    """body_max_length (default unbounded) sets `body` as a CharField(max_length=...) rather than
    a bare TextField(max_length=...) - a TextField's max_length is never enforced by Django (no
    auto-validator, no DB-level constraint), whereas CharField gets both: an automatic
    MaxLengthValidator (full_clean()) and a real varchar(N) column the database itself enforces,
    even if application code bypasses full_clean() entirely (e.g. UserNoteMixin.add_note()'s own
    objects.create())."""

    @isolate_apps("tests.testapp")
    def test_body_max_length_kwarg_sets_the_generated_fields_max_length(self):
        class MaxLengthNoteHost(models.Model):
            class Meta:
                app_label = "testapp"

            notes = notes(user_related_name="max_length_note_host_notes", user_model=EmailUser, body_max_length=280)

        assert MaxLengthNoteHost.notes.model._meta.get_field("body").max_length == 280
        assert isinstance(MaxLengthNoteHost.notes.model._meta.get_field("body"), models.CharField)

    def test_no_body_max_length_leaves_the_field_an_unbounded_text_field(self):
        body_field = Post.notes.model._meta.get_field("body")
        assert body_field.max_length is None
        assert isinstance(body_field, models.TextField)

    @isolate_apps("tests.testapp")
    def test_a_body_over_the_configured_max_length_fails_full_clean(self):
        class MaxLengthNoteHost(models.Model):
            class Meta:
                app_label = "testapp"

            notes = notes(
                user_related_name="max_length_note_host_full_clean_notes", user_model=EmailUser, body_max_length=5
            )

        _create_tables(MaxLengthNoteHost, MaxLengthNoteHost.notes.model)
        host = MaxLengthNoteHost.objects.create()
        alice = EmailUser.objects.create_user(username="alice", email="alice@example.com", password="x")

        note = MaxLengthNoteHost.notes.model(target=host, user=alice, body="x" * 6)
        with pytest.raises(ValidationError):
            note.full_clean()

    @isolate_apps("tests.testapp")
    def test_a_body_over_the_configured_max_length_is_rejected_at_the_database_level(self):
        class MaxLengthNoteHost(models.Model):
            class Meta:
                app_label = "testapp"

            notes = notes(user_related_name="max_length_note_host_db_notes", user_model=EmailUser, body_max_length=5)

        _create_tables(MaxLengthNoteHost, MaxLengthNoteHost.notes.model)
        host = MaxLengthNoteHost.objects.create()
        alice = EmailUser.objects.create_user(username="alice", email="alice@example.com", password="x")

        # Bypasses full_clean() entirely, same as add_note() does - the varchar(5) column itself,
        # not application code, is what stops this.
        with pytest.raises(DataError):
            MaxLengthNoteHost.notes.model.objects.create(target=host, user=alice, body="x" * 6)
