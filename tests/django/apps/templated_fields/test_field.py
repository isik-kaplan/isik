import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from isik.django.apps.templated_fields.delimiters import TemplateDelimiters
from isik.django.apps.templated_fields.field import TemplateCharField, TemplateString, TemplateTextField
from isik.django.apps.templated_fields.policy import TemplatePolicy
from tests.testapp.models import TemplatedPost, default_text_context


pytestmark = pytest.mark.django_db


class _FakeRequest:
    def __init__(self, me):
        self.me = me


@pytest.fixture
def post():
    return TemplatedPost.objects.create(title="hello", default_text="hi {{ title }}", greeting="hi")


class TestStorageAndEquality:
    def test_reading_the_field_back_returns_the_raw_source_not_the_rendered_output(self, post):
        assert post.default_text == "hi {{ title }}"

    def test_reading_the_field_gives_a_template_string(self, post):
        assert isinstance(post.default_text, TemplateString)

    def test_a_blank_value_still_renders_to_an_empty_string(self):
        blank = TemplatedPost(title="x", default_text="", greeting="")
        assert blank.default_text == ""
        assert blank.default_text.render() == ""


class TestRendering:
    def test_render_substitutes_from_available(self, post):
        assert post.default_text.render() == "hi hello"

    def test_render_threads_request_through_to_available(self, post):
        post.default_text = "me={{ me }}"
        assert post.default_text.render(request=_FakeRequest(me="alice")) == "me=alice"

    def test_extra_context_kwargs_are_merged_into_the_available_context(self, post):
        post.default_text = "{{ title }}-{{ extra }}"
        assert post.default_text.render(extra="x") == "hello-x"

    def test_reassigning_the_field_rewraps_immediately(self, post):
        post.default_text = "new {{ title }}"
        assert post.default_text.render() == "new hello"

    def test_refetching_from_the_db_still_renders_correctly(self, post):
        reloaded = TemplatedPost.objects.get(pk=post.pk)
        assert reloaded.default_text.render() == "hi hello"


class TestPolicyEnforcedAtSaveTime:
    """greeting uses TemplatePolicy.VARIABLES_ONLY() - see tests/testapp/models.py."""

    def test_a_for_loop_is_rejected_at_full_clean_time(self, post):
        post.greeting = "{% for x in [1] %}{{ x }}{% endfor %}"
        with pytest.raises(ValidationError):
            post.full_clean()

    def test_a_plain_syntax_error_is_also_rejected_at_full_clean_time(self, post):
        post.greeting = "{% if x %}"
        with pytest.raises(ValidationError):
            post.full_clean()

    def test_plain_variable_substitution_is_accepted(self, post):
        post.greeting = "hi {{ title }}"
        post.full_clean(exclude=["default_text"])

    def test_default_text_accepts_a_for_loop_under_its_own_standard_policy(self, post):
        post.default_text = "{% for x in [1, 2] %}{{ x }}{% endfor %}"
        post.full_clean(exclude=["greeting"])
        assert post.default_text.render() == "12"

    def test_a_blank_value_skips_engine_validation_entirely(self, post):
        # _TemplateFieldMixin.validate()'s `if not value: return` - both fields are blank=True, so
        # full_clean() must accept "" without ever calling engine.validate_syntax() on it.
        post.default_text = ""
        post.greeting = ""
        post.full_clean()


class TestResourceLimitsEnforcedThroughFieldValidate:
    """A bare field not attached to any model (no migration needed) - resource limits go through
    Field.clean()/.validate() the same as the FOR_LOOP/syntax-error branches above."""

    def test_an_oversized_template_source_is_rejected(self):
        field = TemplateCharField(
            max_length=500, available=default_text_context, policy=TemplatePolicy(max_source_length=5)
        )
        with pytest.raises(ValidationError, match="over the 5 limit"):
            field.clean("x" * 100, None)

    def test_a_template_within_the_source_length_limit_is_accepted(self):
        field = TemplateCharField(
            max_length=500, available=default_text_context, policy=TemplatePolicy(max_source_length=50)
        )
        assert field.clean("hi {{ title }}", None) == "hi {{ title }}"

    def test_a_blank_value_skips_engine_validation_when_clean_is_called_directly(self):
        # Model.full_clean() already skips clean() entirely for blank=True empty values (see
        # clean_fields()), so this guard only matters for a bare field's clean() called directly,
        # bypassing that model-level skip.
        field = TemplateCharField(max_length=500, available=default_text_context, blank=True)
        assert field.clean("", None) == ""


def test_positional_args_still_reach_the_underlying_django_field():
    # CharField's own first positional arg is verbose_name - available/policy/etc. are all
    # keyword-only on our side, but *args still has to reach super().__init__() untouched.
    field = TemplateCharField("Default Text", max_length=500, available=default_text_context)
    assert field.verbose_name == "Default Text"


class TestUndefinedResolution:
    """_resolve_undefined()'s explicit/setting/default precedence, at the field layer."""

    def test_field_defaults_to_strict(self):
        field = TemplateCharField(max_length=500, available=default_text_context)
        assert field.undefined == "strict"

    def test_field_undefined_kwarg_overrides_the_default(self):
        field = TemplateCharField(max_length=500, available=default_text_context, undefined="blank")
        assert field.undefined == "blank"

    def test_field_undefined_falls_back_to_the_setting(self):
        with override_settings(TEMPLATE_FIELDS_UNDEFINED="blank"):
            field = TemplateCharField(max_length=500, available=default_text_context)
            assert field.undefined == "blank"

    def test_blank_undefined_renders_a_missing_name_as_empty_string_end_to_end(self, post):
        field = TemplateCharField(max_length=500, available=default_text_context, undefined="blank")
        wrapped = TemplateString(
            "hi {{ missing }}",
            obj=post,
            available=field.available,
            policy=field.policy,
            delimiters=field.delimiters,
            undefined=field.undefined,
        )
        assert wrapped.render() == "hi "


class TestDeconstruct:
    """available/policy/delimiters/undefined must all survive a deconstruct() -> reconstruct()
    roundtrip - what migration serialization actually relies on."""

    def test_char_field_deconstruct_roundtrips(self):
        custom_delimiters = TemplateDelimiters(variable_start_string="<<", variable_end_string=">>")
        field = TemplateCharField(
            max_length=123,
            available=default_text_context,
            policy=TemplatePolicy.PERMISSIVE(),
            delimiters=custom_delimiters,
            undefined="blank",
        )
        _name, _path, args, kwargs = field.deconstruct()
        assert kwargs["delimiters"] == custom_delimiters
        reconstructed = TemplateCharField(*args, **kwargs)
        assert reconstructed.max_length == 123
        assert reconstructed.available is default_text_context
        assert reconstructed.policy == TemplatePolicy.PERMISSIVE()
        assert reconstructed.delimiters == custom_delimiters
        assert reconstructed.undefined == "blank"

    def test_text_field_deconstruct_roundtrips(self):
        field = TemplateTextField(available=default_text_context, policy=TemplatePolicy.VARIABLES_ONLY())
        _name, _path, args, kwargs = field.deconstruct()
        reconstructed = TemplateTextField(*args, **kwargs)
        assert reconstructed.available is default_text_context
        assert reconstructed.policy == TemplatePolicy.VARIABLES_ONLY()
        assert reconstructed.undefined == "strict"
