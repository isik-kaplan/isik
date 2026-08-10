import pytest
from rest_framework import status
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import ModelViewSet

from isik.django.apps.templated_fields.drf import TemplateFieldPreviewMixin, TemplateFieldsModelSerializerMixin
from isik.django.drf.serializers.base import BaseModelSerializer
from tests.testapp.models import TemplatedPost


pytestmark = pytest.mark.django_db


class _FakeRequest:
    def __init__(self, me):
        self.me = me


# Built once at module scope, like real usage - ModelSerializerRegistryMixin raises if the same
# model is registered twice, so calling this fresh in every test would collide on the second call.
class TemplatedPostSerializer(TemplateFieldsModelSerializerMixin, BaseModelSerializer):
    class Meta:
        model = TemplatedPost
        fields = ["id", "title", "default_text", "greeting"]


def _serializer_cls():
    return TemplatedPostSerializer


@pytest.fixture
def post():
    return TemplatedPost.objects.create(title="hello", default_text="hi {{ title }}", greeting="hi")


class TestReading:
    def test_reads_as_raw_and_rendered(self, post):
        Serializer = _serializer_cls()
        data = Serializer(post).data
        assert data["default_text"] == {"raw": "hi {{ title }}", "rendered": "hi hello"}

    def test_request_in_context_is_available_to_the_render(self, post):
        post.default_text = "me={{ me }}"
        Serializer = _serializer_cls()
        data = Serializer(post, context={"request": _FakeRequest(me="alice")}).data
        assert data["default_text"] == {"raw": "me={{ me }}", "rendered": "me=alice"}

    def test_a_blank_field_reads_as_empty_raw_and_rendered(self):
        blank = TemplatedPost.objects.create(title="x", default_text="", greeting="")
        Serializer = _serializer_cls()
        data = Serializer(blank).data
        assert data["default_text"] == {"raw": "", "rendered": ""}

    def test_a_render_time_error_degrades_to_a_null_rendered_value_instead_of_raising(self, post):
        # Stored raw text can be syntax/policy-valid yet still fail at render time (e.g. a name
        # available() doesn't provide, under strict undefined) - reading a list of hosts shouldn't
        # 500 because one row's template is broken.
        post.default_text = "{{ totally_missing }}"
        post.save()
        Serializer = _serializer_cls()
        data = Serializer(post).data
        assert data["default_text"] == {"raw": "{{ totally_missing }}", "rendered": None}


class TestWriting:
    def test_write_takes_a_plain_string_not_the_read_shape(self, post):
        Serializer = _serializer_cls()
        serializer = Serializer(
            post, data={"title": post.title, "default_text": "bye {{ title }}", "greeting": post.greeting}
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        assert post.default_text == "bye {{ title }}"

    def test_writing_a_blank_value_clears_the_field_without_running_it_through_validate_syntax(self, post):
        Serializer = _serializer_cls()
        serializer = Serializer(post, data={"title": post.title, "default_text": "", "greeting": post.greeting})
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        assert post.default_text == ""

    def test_writing_a_policy_violating_template_fails_drf_validation_directly(self, post):
        # greeting uses TemplatePolicy.VARIABLES_ONLY() - a for-loop is rejected right here in
        # serializer.errors, not only later at .save() via the model's full_clean().
        Serializer = _serializer_cls()
        serializer = Serializer(
            post,
            data={"title": post.title, "default_text": post.default_text, "greeting": "{% for x in [1] %}{% endfor %}"},
        )
        assert not serializer.is_valid()
        assert "greeting" in serializer.errors
        assert "TemplateFeature.FOR_LOOP" in str(serializer.errors["greeting"][0])

    def test_writing_a_syntactically_broken_template_fails_drf_validation_directly(self, post):
        Serializer = _serializer_cls()
        serializer = Serializer(
            post, data={"title": post.title, "default_text": "{% if x %}", "greeting": post.greeting}
        )
        assert not serializer.is_valid()
        assert "default_text" in serializer.errors

    def test_a_too_long_value_still_fails_ordinary_charfield_validation(self, post):
        # greeting has max_length=200 (tests/testapp/models.py) - "x" * 201 is syntactically valid
        # Jinja (no braces at all) but violates the plain CharField check.
        Serializer = _serializer_cls()
        serializer = Serializer(
            post, data={"title": post.title, "default_text": post.default_text, "greeting": "x" * 201}
        )
        assert not serializer.is_valid()
        assert "greeting" in serializer.errors


class TemplatedPostViewSet(TemplateFieldPreviewMixin, ModelViewSet):
    queryset = TemplatedPost.objects.all()
    serializer_class = _serializer_cls()


@pytest.fixture
def preview_view():
    return TemplatedPostViewSet.as_view({"post": "preview_template"})


class TestPreviewAction:
    def test_renders_the_given_raw_value_against_the_instance_without_saving(self, post, preview_view):
        request = APIRequestFactory().post(
            f"/templated-posts/{post.pk}/preview-template/", {"field": "default_text", "raw": "new {{ title }}"}
        )
        response = preview_view(request, pk=post.pk)
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"rendered": "new hello"}
        assert TemplatedPost.objects.get(pk=post.pk).default_text == "hi {{ title }}"

    def test_a_policy_violating_raw_value_returns_400(self, post, preview_view):
        request = APIRequestFactory().post(
            f"/templated-posts/{post.pk}/preview-template/",
            {"field": "greeting", "raw": "{% for x in [1] %}{% endfor %}"},
        )
        response = preview_view(request, pk=post.pk)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "raw" in response.data

    def test_a_field_that_is_not_a_template_field_returns_400(self, post, preview_view):
        request = APIRequestFactory().post(
            f"/templated-posts/{post.pk}/preview-template/", {"field": "title", "raw": "x"}
        )
        response = preview_view(request, pk=post.pk)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "field" in response.data

    def test_a_field_that_does_not_exist_returns_400(self, post, preview_view):
        request = APIRequestFactory().post(
            f"/templated-posts/{post.pk}/preview-template/", {"field": "nope", "raw": "x"}
        )
        response = preview_view(request, pk=post.pk)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "field" in response.data

    def test_an_undefined_variable_in_the_raw_value_returns_400(self, post, preview_view):
        # default_text_context() only ever provides "title"/"me" - "totally_missing" isn't one of
        # them, so strict undefined (the field's default) raises jinja2.UndefinedError.
        request = APIRequestFactory().post(
            f"/templated-posts/{post.pk}/preview-template/",
            {"field": "default_text", "raw": "{{ totally_missing }}"},
        )
        response = preview_view(request, pk=post.pk)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "raw" in response.data

    def test_request_is_threaded_through_to_available(self, post, preview_view):
        request = APIRequestFactory().post(
            f"/templated-posts/{post.pk}/preview-template/", {"field": "default_text", "raw": "me={{ me }}"}
        )
        response = preview_view(request, pk=post.pk)
        # default_text_context() sets "me" from getattr(request, "me", None) - a bare
        # APIRequestFactory request has none, so "me" is a legitimately-provided None (renders as
        # the literal text "None", not empty - StrictUndefined only complains about a *missing*
        # context key, not one that's present with a None value).
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"rendered": "me=None"}
