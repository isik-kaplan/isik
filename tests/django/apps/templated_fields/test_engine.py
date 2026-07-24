import jinja2
import pytest
from jinja2.exceptions import SecurityError, TemplateAssertionError

from isik.django.apps.templated_fields.delimiters import TemplateDelimiters
from isik.django.apps.templated_fields.engine import TemplateSecurityError, render, validate_syntax
from isik.django.apps.templated_fields.policy import TemplateFeature, TemplatePolicy


DEFAULT_DELIMITERS = TemplateDelimiters()


def _render(source, *, policy=None, context=None, undefined="strict", delimiters=None):
    return render(
        source,
        delimiters=delimiters or DEFAULT_DELIMITERS,
        policy=policy or TemplatePolicy.STANDARD(),
        context=context or {},
        undefined=undefined,
    )


class TestPlainOutputAndComments:
    """Neither is individually gateable in Jinja - always available regardless of policy."""

    def test_variable_substitution_works_under_variables_only(self):
        result = _render("hello {{ name }}", policy=TemplatePolicy.VARIABLES_ONLY(), context={"name": "world"})
        assert result == "hello world"

    def test_comments_are_stripped_even_under_variables_only(self):
        assert _render("{# a comment #}hi", policy=TemplatePolicy.VARIABLES_ONLY()) == "hi"


class TestFeatureGating:
    def test_for_loop_rejected_under_variables_only(self):
        with pytest.raises(TemplateSecurityError, match="TemplateFeature.FOR_LOOP"):
            _render("{% for x in xs %}{{ x }}{% endfor %}", policy=TemplatePolicy.VARIABLES_ONLY(), context={"xs": [1]})

    def test_for_loop_allowed_under_standard(self):
        assert _render("{% for x in xs %}{{ x }}{% endfor %}", context={"xs": [1, 2]}) == "12"

    def test_conditional_rejected_under_variables_only(self):
        with pytest.raises(TemplateSecurityError, match="TemplateFeature.CONDITIONAL"):
            _render("{% if x %}y{% endif %}", policy=TemplatePolicy.VARIABLES_ONLY(), context={"x": True})

    def test_conditional_allowed_under_standard(self):
        assert _render("{% if x %}y{% else %}n{% endif %}", context={"x": False}) == "n"

    def test_macro_rejected_under_standard_but_allowed_under_permissive(self):
        source = "{% macro greet(n) %}hi {{ n }}{% endmacro %}{{ greet('bob') }}"
        with pytest.raises(TemplateSecurityError, match="TemplateFeature.MACRO"):
            _render(source)
        assert _render(source, policy=TemplatePolicy.PERMISSIVE()) == "hi bob"

    def test_set_rejected_under_standard_but_allowed_under_permissive(self):
        source = "{% set x = 1 %}{{ x }}"
        with pytest.raises(TemplateSecurityError, match="TemplateFeature.SET"):
            _render(source)
        assert _render(source, policy=TemplatePolicy.PERMISSIVE()) == "1"

    def test_loop_controls_rejected_under_standard_but_allowed_under_permissive(self):
        # {% break %}/{% continue %} need jinja2.ext.loopcontrols to even parse - without
        # LOOP_CONTROLS enabled, the extension isn't loaded at all, so this fails as a plain
        # TemplateSyntaxError (unknown tag) rather than reaching our own AST policy check.
        source = "{% for x in xs %}{% if x == 2 %}{% break %}{% endif %}{{ x }}{% endfor %}"
        with pytest.raises(jinja2.TemplateSyntaxError):
            _render(source, context={"xs": [1, 2, 3]})
        assert _render(source, policy=TemplatePolicy.PERMISSIVE(), context={"xs": [1, 2, 3]}) == "1"


class TestAlwaysBlocked:
    """include/import/from-import/extends are never policy-gated - rejected even under PERMISSIVE."""

    @pytest.mark.parametrize(
        "source",
        [
            "{% include 'x.html' %}",
            "{% extends 'x.html' %}",
            "{% import 'x.html' as x %}",
            "{% from 'x.html' import x %}",
        ],
    )
    def test_rejected_even_under_permissive(self, source):
        with pytest.raises(TemplateSecurityError):
            _render(source, policy=TemplatePolicy.PERMISSIVE())


class TestSandboxing:
    """SandboxedEnvironment's job, not the AST allowlist's - a separate layer from feature gating."""

    def test_dunder_attribute_access_is_blocked(self):
        with pytest.raises(SecurityError):
            _render("{{ ''.__class__ }}", policy=TemplatePolicy.PERMISSIVE())


class TestFilters:
    def test_filters_disallowed_by_default_denylist(self):
        with pytest.raises(TemplateAssertionError):
            _render("{{ x|safe }}", context={"x": "<b>"})

    def test_ordinary_builtin_filter_works_by_default(self):
        assert _render("{{ x|upper }}", context={"x": "hi"}) == "HI"

    def test_filters_rejected_entirely_when_feature_disabled(self):
        with pytest.raises(TemplateAssertionError):
            _render("{{ x|upper }}", policy=TemplatePolicy.VARIABLES_ONLY(), context={"x": "hi"})

    def test_allowed_filters_narrows_below_the_default_denylist_complement(self):
        policy = TemplatePolicy(features=[TemplateFeature.FILTERS], allowed_filters=frozenset({"lower"}))
        assert _render("{{ x|lower }}", policy=policy, context={"x": "HI"}) == "hi"
        with pytest.raises(TemplateAssertionError):
            _render("{{ x|upper }}", policy=policy, context={"x": "hi"})


class TestDroppedGlobals:
    """namespace/lipsum/cycler/joiner are popped from env.globals regardless of policy - see
    DROPPED_GLOBALS in policy.py."""

    @pytest.mark.parametrize(
        "source", ["{{ namespace(x=1) }}", "{{ lipsum(1) }}", "{{ cycler('a', 'b') }}", "{{ joiner() }}"]
    )
    def test_dropped_globals_are_unreachable_even_under_permissive(self, source):
        with pytest.raises(jinja2.UndefinedError):
            _render(source, policy=TemplatePolicy.PERMISSIVE())


class TestFilterBlock:
    def test_filter_block_statement_works_when_filters_are_enabled(self):
        assert _render("{% filter upper %}hi{% endfilter %}") == "HI"

    def test_filter_block_statement_rejected_when_filters_disabled(self):
        with pytest.raises(TemplateAssertionError):
            _render("{% filter upper %}hi{% endfilter %}", policy=TemplatePolicy.VARIABLES_ONLY())


class TestTests:
    def test_ordinary_builtin_test_works_by_default(self):
        assert _render("{{ 'y' if x is none else 'n' }}", context={"x": None}) == "y"

    def test_tests_rejected_entirely_when_feature_disabled(self):
        with pytest.raises(TemplateAssertionError):
            _render("{{ x is none }}", policy=TemplatePolicy.VARIABLES_ONLY(), context={"x": None})

    def test_allowed_tests_narrows_below_the_default_complement(self):
        policy = TemplatePolicy(features=[TemplateFeature.TESTS], allowed_tests=frozenset({"none"}))
        assert _render("{{ 'y' if x is none else 'n' }}", policy=policy, context={"x": None}) == "y"
        with pytest.raises(TemplateAssertionError):
            _render("{{ x is defined }}", policy=policy, context={"x": None})


class TestUndefined:
    def test_strict_raises_on_a_name_available_did_not_provide(self):
        with pytest.raises(jinja2.UndefinedError):
            _render("{{ missing }}", undefined="strict")

    def test_blank_renders_empty_string_instead(self):
        assert _render("before {{ missing }} after", undefined="blank") == "before  after"


class TestResourceLimits:
    def test_max_source_length_rejects_an_oversized_template(self):
        policy = TemplatePolicy(max_source_length=5)
        with pytest.raises(TemplateSecurityError, match="over the 5 limit"):
            _render("x" * 100, policy=policy)

    def test_max_loop_iterations_caps_range(self):
        policy = TemplatePolicy(features=[TemplateFeature.FOR_LOOP], max_loop_iterations=3)
        with pytest.raises(TemplateSecurityError, match="over the 3 limit"):
            _render("{% for i in range(10) %}{{ i }}{% endfor %}", policy=policy)

    def test_max_loop_iterations_does_not_reject_a_range_within_the_cap(self):
        policy = TemplatePolicy(features=[TemplateFeature.FOR_LOOP], max_loop_iterations=10)
        assert _render("{% for i in range(3) %}{{ i }}{% endfor %}", policy=policy) == "012"

    def test_max_render_length_aborts_a_runaway_loop(self):
        policy = TemplatePolicy(features=[TemplateFeature.FOR_LOOP], max_render_length=5, max_loop_iterations=None)
        with pytest.raises(TemplateSecurityError, match="exceeded the 5 char limit"):
            _render("{% for i in range(1000) %}x{% endfor %}", policy=policy)

    def test_max_render_length_none_does_not_cap_output(self):
        policy = TemplatePolicy(features=[TemplateFeature.FOR_LOOP], max_render_length=None, max_loop_iterations=None)
        assert _render("{% for i in range(20) %}x{% endfor %}", policy=policy) == "x" * 20

    def test_max_source_length_none_does_not_cap_source(self):
        policy = TemplatePolicy(max_source_length=None, max_render_length=None)
        assert _render("x" * 20_000, policy=policy) == "x" * 20_000


class TestDelimiters:
    def test_custom_delimiters_change_the_placeholder_syntax(self):
        custom = TemplateDelimiters(variable_start_string="<<", variable_end_string=">>")
        assert _render("hi <<name>>", delimiters=custom, context={"name": "bob"}) == "hi bob"

    def test_default_delimiter_syntax_is_inert_text_under_custom_delimiters(self):
        custom = TemplateDelimiters(variable_start_string="<<", variable_end_string=">>")
        assert _render("{{ name }} <<name>>", delimiters=custom, context={"name": "bob"}) == "{{ name }} bob"


class TestValidateSyntax:
    def test_raises_the_same_error_a_render_would(self):
        with pytest.raises(TemplateSecurityError):
            validate_syntax(
                "{% for x in xs %}{{ x }}{% endfor %}",
                delimiters=DEFAULT_DELIMITERS,
                policy=TemplatePolicy.VARIABLES_ONLY(),
                undefined="strict",
            )

    def test_does_not_require_a_render_context(self):
        validate_syntax(
            "{% for x in xs %}{{ x }}{% endfor %}",
            delimiters=DEFAULT_DELIMITERS,
            policy=TemplatePolicy.STANDARD(),
            undefined="strict",
        )

    def test_plain_syntax_errors_surface_as_jinja_template_syntax_error(self):
        with pytest.raises(jinja2.TemplateSyntaxError):
            validate_syntax(
                "{% if x %}", delimiters=DEFAULT_DELIMITERS, policy=TemplatePolicy.STANDARD(), undefined="strict"
            )
