import dataclasses

import pytest
from django.test import override_settings

from isik.django.apps.templated_fields.policy import TemplateFeature, TemplatePolicy, resolve_policy


def test_variables_only_has_no_control_flow_features():
    assert TemplatePolicy.VARIABLES_ONLY().features == frozenset()


def test_standard_adds_filters_tests_conditional_and_for_loop():
    assert TemplatePolicy.STANDARD().features == frozenset(
        {TemplateFeature.FILTERS, TemplateFeature.TESTS, TemplateFeature.CONDITIONAL, TemplateFeature.FOR_LOOP}
    )


def test_permissive_adds_loop_controls_macro_and_set_on_top_of_standard():
    assert TemplatePolicy.PERMISSIVE().features == TemplatePolicy.STANDARD().features | {
        TemplateFeature.LOOP_CONTROLS,
        TemplateFeature.MACRO,
        TemplateFeature.SET,
    }


def test_presets_are_cached_singletons():
    assert TemplatePolicy.STANDARD() is TemplatePolicy.STANDARD()
    assert TemplatePolicy.STANDARD() is not TemplatePolicy.PERMISSIVE()


def test_features_kwarg_accepts_a_plain_list_not_just_a_frozenset():
    policy = TemplatePolicy(features=[TemplateFeature.FOR_LOOP, TemplateFeature.CONDITIONAL])
    assert policy.features == frozenset({TemplateFeature.FOR_LOOP, TemplateFeature.CONDITIONAL})


def test_contains_checks_feature_membership():
    policy = TemplatePolicy(features=[TemplateFeature.FOR_LOOP])
    assert TemplateFeature.FOR_LOOP in policy
    assert TemplateFeature.MACRO not in policy


def test_policy_is_frozen():
    policy = TemplatePolicy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.max_render_length = 1


def test_deconstruct_returns_a_reconstructable_path_and_kwargs():
    policy = TemplatePolicy.STANDARD()
    path, args, kwargs = policy.deconstruct()
    assert path == "isik.django.apps.templated_fields.policy.TemplatePolicy"
    assert args == []
    assert TemplatePolicy(**kwargs) == policy


def test_resolve_policy_prefers_explicit_over_setting():
    explicit = TemplatePolicy.PERMISSIVE()
    assert resolve_policy(explicit) is explicit


def test_resolve_policy_falls_back_to_setting_then_standard():
    assert resolve_policy(None) == TemplatePolicy.STANDARD()

    custom = TemplatePolicy.VARIABLES_ONLY()
    with override_settings(TEMPLATE_FIELDS_POLICY=custom):
        assert resolve_policy(None) is custom
