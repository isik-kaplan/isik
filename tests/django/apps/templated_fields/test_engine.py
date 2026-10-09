import jinja2
import pytest
from jinja2.exceptions import SecurityError, TemplateAssertionError

from isik.django.apps.templated_fields import engine
from isik.django.apps.templated_fields.delimiters import TemplateDelimiters
from isik.django.apps.templated_fields.engine import TemplateSecurityError, render, validate_syntax
from isik.django.apps.templated_fields.policy import TemplateFeature, TemplatePolicy


DEFAULT_DELIMITERS = TemplateDelimiters()


@pytest.fixture(autouse=True)
def _reset_engine_caches():
    # _build_environment/_compile are module-level @lru_cache-d, keyed on (delimiters, policy,
    # ..., source) equality, not identity - two tests (or two reruns of the same test under a
    # tool that reuses the process, e.g. mutation testing) using an equal cache key would
    # otherwise silently reuse whatever env/compiled template the first call built.
    engine._build_environment.cache_clear()
    engine._compile.cache_clear()


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
        with pytest.raises(TemplateSecurityError, match=r"^For requires TemplateFeature\.FOR_LOOP, not enabled"):
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
        with pytest.raises(TemplateSecurityError, match="is never allowed in a template field"):
            _render(source, policy=TemplatePolicy.PERMISSIVE())

    def test_the_error_names_the_actual_blocked_node_type(self):
        # Not just "NoneType" or some other stand-in - type(node), not type(None).
        with pytest.raises(TemplateSecurityError, match=r"^Include is never allowed in a template field$"):
            _render("{% include 'x.html' %}", policy=TemplatePolicy.PERMISSIVE())


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
        with pytest.raises(TemplateSecurityError, match=r"^range\(\) would iterate 10 times, over the 3 limit$"):
            _render("{% for i in range(10) %}{{ i }}{% endfor %}", policy=policy)

    def test_max_loop_iterations_does_not_reject_a_range_within_the_cap(self):
        policy = TemplatePolicy(features=[TemplateFeature.FOR_LOOP], max_loop_iterations=10)
        assert _render("{% for i in range(3) %}{{ i }}{% endfor %}", policy=policy) == "012"

    def test_max_loop_iterations_exactly_at_the_limit_does_not_raise(self):
        # > vs >= - a range whose length exactly equals the limit must still be allowed.
        policy = TemplatePolicy(features=[TemplateFeature.FOR_LOOP], max_loop_iterations=3)
        assert _render("{% for i in range(3) %}{{ i }}{% endfor %}", policy=policy) == "012"

    def test_max_render_length_aborts_a_runaway_loop(self):
        policy = TemplatePolicy(features=[TemplateFeature.FOR_LOOP], max_render_length=5, max_loop_iterations=None)
        with pytest.raises(TemplateSecurityError, match=r"^rendered output exceeded the 5 char limit$"):
            _render("{% for i in range(1000) %}x{% endfor %}", policy=policy)

    def test_max_render_length_exactly_at_the_limit_does_not_raise(self):
        policy = TemplatePolicy(features=[TemplateFeature.FOR_LOOP], max_render_length=5, max_loop_iterations=None)
        assert _render("{% for i in range(5) %}x{% endfor %}", policy=policy) == "xxxxx"

    def test_max_render_length_none_does_not_cap_output(self):
        policy = TemplatePolicy(features=[TemplateFeature.FOR_LOOP], max_render_length=None, max_loop_iterations=None)
        assert _render("{% for i in range(20) %}x{% endfor %}", policy=policy) == "x" * 20

    def test_max_source_length_none_does_not_cap_source(self):
        policy = TemplatePolicy(max_source_length=None, max_render_length=None)
        assert _render("x" * 20_000, policy=policy) == "x" * 20_000


LOOPS = [TemplateFeature.FOR_LOOP, TemplateFeature.CONDITIONAL]


def _loops(limit, **kwargs):
    return TemplatePolicy(features=LOOPS, max_loop_iterations=limit, **kwargs)


class TestLoopBudget:
    """max_loop_iterations counts every {% for %} iteration of one render, not each range() alone."""

    def test_nested_loops_count_together_even_with_no_output(self):
        source = (
            "{% for a in range(1000) %}{% for b in range(1000) %}{% for c in range(1000) %}"
            "{% endfor %}{% endfor %}{% endfor %}"
        )
        with pytest.raises(
            TemplateSecurityError, match=r"^the loops in this template ran over the 1000 iteration limit$"
        ):
            _render(source)

    def test_an_outer_and_its_inner_iterations_all_count(self):
        # 2 outer + 2 * 3 inner = 8.
        source = "{% for a in range(2) %}{% for b in range(3) %}{% endfor %}{% endfor %}done"
        assert _render(source, policy=_loops(8)) == "done"
        with pytest.raises(TemplateSecurityError, match="over the 7 iteration limit"):
            _render(source, policy=_loops(7))

    def test_loops_one_after_another_share_the_budget(self):
        source = "{% for a in range(3) %}{{ a }}{% endfor %}{% for b in range(3) %}{{ b }}{% endfor %}"
        assert _render(source, policy=_loops(6)) == "012012"
        with pytest.raises(TemplateSecurityError):
            _render(source, policy=_loops(5))

    def test_a_loop_over_a_literal_counts_too(self):
        source = '{% for a in "abcd" %}{% for b in "abcd" %}{% endfor %}{% endfor %}'
        with pytest.raises(TemplateSecurityError):
            _render(source, policy=_loops(19))
        assert _render(source, policy=_loops(20)) == ""

    def test_a_loop_over_the_context_counts_too(self):
        with pytest.raises(TemplateSecurityError):
            _render("{% for a in items %}{% endfor %}", policy=_loops(2), context={"items": [1, 2, 3]})

    def test_iterations_skipped_by_a_loop_filter_still_count(self):
        source = "{% for a in range(5) if a > 3 %}{{ a }}{% endfor %}"
        assert _render(source, policy=_loops(5)) == "4"
        with pytest.raises(TemplateSecurityError):
            _render(source, policy=_loops(4))

    def test_every_render_starts_a_fresh_count(self):
        source = "{% for a in range(3) %}{{ a }}{% endfor %}"
        assert _render(source, policy=_loops(3)) == "012"
        assert _render(source, policy=_loops(3)) == "012"

    def test_the_count_works_without_a_render_length_limit(self):
        with pytest.raises(TemplateSecurityError):
            _render("{% for a in range(3) %}{% endfor %}", policy=_loops(2, max_render_length=None))

    def test_loop_variables_still_work(self):
        source = (
            "{% for a in range(3) %}{{ loop.index }}/{{ loop.length }}{% if not loop.last %},{% endif %}{% endfor %}"
        )
        assert _render(source, policy=_loops(3)) == "1/3,2/3,3/3"

    def test_no_limit_means_no_count(self):
        source = '{% for a in "abcdefgh" %}{% for b in "abcdefgh" %}{% endfor %}{% endfor %}ok'
        assert _render(source, policy=_loops(None)) == "ok"

    def test_a_loop_with_an_else_branch_still_takes_it(self):
        assert _render("{% for a in [] %}x{% else %}empty{% endfor %}", policy=_loops(1)) == "empty"


class TestLargeValues:
    """max_render_length also refuses a `*` or `**` result past it, before computing the result."""

    @pytest.mark.parametrize(
        "source", ['{{ "x" * 400000000 }}', '{{ 400000000 * "x" }}', "{{ [1] * 400000000 }}", "{{ (1,) * 400000000 }}"]
    )
    def test_a_repeated_sequence_past_the_limit_is_refused(self, source):
        with pytest.raises(TemplateSecurityError, match=r"^\* would build a value over the 10000 char limit$"):
            _render(source)

    def test_a_repeated_sequence_at_the_limit_renders(self):
        policy = TemplatePolicy(max_render_length=6)
        assert _render('{{ "ab" * 3 }}', policy=policy) == "ababab"
        with pytest.raises(TemplateSecurityError):
            _render('{{ "ab" * 4 }}', policy=policy)

    def test_a_huge_power_is_refused(self):
        with pytest.raises(TemplateSecurityError, match=r"^\*\* would build a value over the 10000 char limit$"):
            _render("{{ 9 ** (9 ** 9) }}")

    def test_a_power_is_measured_by_its_digits(self):
        # 8 is 4 bits, at most 2 digits; 3 of those is 6.
        policy = TemplatePolicy(max_render_length=6, max_source_length=None)
        assert _render("{{ 8 ** 3 }}", policy=policy) == "512"
        with pytest.raises(TemplateSecurityError):
            _render("{{ 8 ** 4 }}", policy=policy)

    def test_a_value_already_over_the_limit_is_refused_to_the_first_power(self):
        with pytest.raises(TemplateSecurityError):
            _render("{{ big ** 1 }}", policy=TemplatePolicy(max_render_length=5), context={"big": 10**20})

    def test_a_product_of_ints_is_measured_by_both_sides(self):
        # 99 is 7 bits, at most 3 digits, so 99 * 99 is at most 6.
        policy = TemplatePolicy(max_render_length=6)
        assert _render("{{ 99 * 99 }}", policy=policy) == "9801"
        with pytest.raises(TemplateSecurityError):
            _render("{{ 99 * 999 }}", policy=policy)

    def test_other_operands_pass_untouched(self):
        policy = TemplatePolicy(features=[TemplateFeature.CONDITIONAL], max_render_length=0)
        assert _render("{% if 1.5 * 2 == 3.0 and 2.0 ** 2 == 4.0 %}{% endif %}", policy=policy) == ""

    def test_no_render_length_limit_means_no_check(self):
        policy = TemplatePolicy(max_render_length=None)
        assert _render('{{ "x" * 20000 }}', policy=policy) == "x" * 20000

    @pytest.mark.parametrize(("number", "digits"), [(0, 1), (1, 1), (7, 2), (8, 2), (-8, 2), (999, 4)])
    def test_digits_is_an_upper_bound_from_the_bit_length(self, number, digits):
        assert engine._digits(number) == digits


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


class TestSourceLengthAndEscaping:
    def test_a_source_exactly_at_the_limit_is_accepted_and_one_over_is_refused_saying_so(self):
        policy = TemplatePolicy(max_source_length=5)

        assert _render("hello", policy=policy) == "hello"
        with pytest.raises(TemplateSecurityError, match=r"^template source is 6 chars, over the 5 limit$"):
            _render("hello!", policy=policy)

    def test_values_are_html_escaped(self):
        assert _render("{{ x }}", context={"x": "<b>&</b>"}) == "&lt;b&gt;&amp;&lt;/b&gt;"
