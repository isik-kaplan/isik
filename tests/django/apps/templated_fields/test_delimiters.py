from django.test import override_settings

from isik.django.apps.templated_fields.delimiters import TemplateDelimiters, resolve_delimiters


def test_defaults_match_jinjas_own_delimiters():
    assert TemplateDelimiters().as_environment_kwargs() == {
        "variable_start_string": "{{",
        "variable_end_string": "}}",
        "block_start_string": "{%",
        "block_end_string": "%}",
        "comment_start_string": "{#",
        "comment_end_string": "#}",
    }


def test_resolve_delimiters_prefers_explicit_over_setting():
    explicit = TemplateDelimiters(variable_start_string="<<", variable_end_string=">>")
    assert resolve_delimiters(explicit) is explicit


def test_resolve_delimiters_falls_back_to_setting_then_default():
    assert resolve_delimiters(None) == TemplateDelimiters()

    custom = TemplateDelimiters(variable_start_string="<<", variable_end_string=">>")
    with override_settings(TEMPLATE_FIELDS_DELIMITERS=custom):
        assert resolve_delimiters(None) is custom


def test_deconstruct_roundtrips():
    delimiters = TemplateDelimiters(variable_start_string="<<", variable_end_string=">>")
    path, args, kwargs = delimiters.deconstruct()
    assert path == "isik.django.apps.templated_fields.delimiters.TemplateDelimiters"
    assert args == []
    assert TemplateDelimiters(**kwargs) == delimiters
