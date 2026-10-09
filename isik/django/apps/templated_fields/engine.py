"""
Renders a template field's Jinja source under a `TemplatePolicy`/`TemplateDelimiters`: a
`SandboxedEnvironment` for attribute-access safety, plus the AST-level allowlist from `policy.py`
for which statements are legal at all - see `TemplatePolicy`'s docstring for why both layers exist.
`_build_environment`/`_compile` are cached, since delimiters/policy combinations are few and
distinct template sources tend to repeat across renders of the same field value.
"""

import functools

from jinja2 import StrictUndefined, Undefined, nodes
from jinja2.sandbox import SandboxedEnvironment

from isik._internal.translation import gettext as _
from isik.django.apps.templated_fields.policy import (
    ALWAYS_BLOCKED_NODES,
    DROPPED_GLOBALS,
    FEATURE_NODES,
    FILTER_DENYLIST,
    TemplateFeature,
)


class TemplateSecurityError(Exception):
    """Raised when a template uses syntax its `TemplatePolicy` doesn't permit, or trips one of its
    resource limits (`max_source_length`/`max_render_length`/`max_loop_iterations`)."""


_UNDEFINED_CLASSES = {"strict": StrictUndefined, "blank": Undefined}

# Where a render keeps its loop count - not an identifier, so no template can name it.
_LOOP_COUNT = "isik:loop count"


def _digits(number):
    # An upper bound on len(str(number)), from its bit length - converting a huge int to find out
    # would be the very work this avoids.
    return number.bit_length() // 3 + 1


def _result_length(operator, left, right):
    """An upper bound on how long `left <operator> right` would be, worked out without computing it."""
    if operator == "*":
        for sequence, count in ((left, right), (right, left)):
            if isinstance(sequence, (str, list, tuple)) and isinstance(count, int):
                return len(sequence) * count
        if isinstance(left, int) and isinstance(right, int):
            return _digits(left) + _digits(right)
    elif isinstance(left, int) and isinstance(right, int):
        return right * _digits(left)
    return 0


class _Environment(SandboxedEnvironment):
    # Neither is folded at compile time once intercepted, so call_binop() sees every one.
    intercepted_binops = frozenset({"*", "**"})

    def __init__(self, *, policy, **kwargs):
        self.isik_policy = policy
        super().__init__(**kwargs)

    def call_binop(self, context, operator, left, right):
        limit = self.isik_policy.max_render_length
        if limit is not None and _result_length(operator, left, right) > limit:
            raise TemplateSecurityError(
                _("%(operator)s would build a value over the %(limit)s char limit")
                % {"operator": operator, "limit": limit}
            )
        return self.binop_table[operator](left, right)

    def count_loop(self, context, iterable):
        # Every {% for %}'s iterable goes through here (see _compile), so one count covers the whole
        # render: nested loops multiply, and a loop that writes nothing still counts.
        count = context[_LOOP_COUNT]
        limit = self.isik_policy.max_loop_iterations
        for item in iterable:
            count[0] += 1
            if count[0] > limit:
                raise TemplateSecurityError(
                    _("the loops in this template ran over the %(limit)s iteration limit") % {"limit": limit}
                )
            yield item


def _capped_range(limit):
    def capped_range(*args):
        result = range(*args)
        if limit is not None and len(result) > limit:
            raise TemplateSecurityError(
                _("range() would iterate %(count)s times, over the %(limit)s limit")
                % {"count": len(result), "limit": limit}
            )
        return result

    return capped_range


@functools.lru_cache(maxsize=64)
def _build_environment(delimiters, policy, undefined):
    extensions = ["jinja2.ext.loopcontrols"] if TemplateFeature.LOOP_CONTROLS in policy else []
    env = _Environment(
        policy=policy,
        autoescape=True,
        extensions=extensions,
        undefined=_UNDEFINED_CLASSES[undefined],
        **delimiters.as_environment_kwargs(),
    )

    for name in DROPPED_GLOBALS:
        del env.globals[name]
    if TemplateFeature.FOR_LOOP in policy:
        env.globals["range"] = _capped_range(policy.max_loop_iterations)
    else:
        del env.globals["range"]

    allowed_filters = policy.allowed_filters
    if TemplateFeature.FILTERS not in policy:
        allowed_filters = frozenset()
    elif allowed_filters is None:
        allowed_filters = frozenset(env.filters) - FILTER_DENYLIST
    env.filters = {name: fn for name, fn in env.filters.items() if name in allowed_filters}

    allowed_tests = policy.allowed_tests
    if TemplateFeature.TESTS not in policy:
        allowed_tests = frozenset()
    elif allowed_tests is None:
        allowed_tests = frozenset(env.tests)
    env.tests = {name: fn for name, fn in env.tests.items() if name in allowed_tests}

    return env


def _check_policy(ast, policy):
    for node in ast.find_all(ALWAYS_BLOCKED_NODES):
        raise TemplateSecurityError(_("%(node)s is never allowed in a template field") % {"node": type(node).__name__})
    for feature, node_types in FEATURE_NODES.items():
        if feature in policy:
            continue
        for node in ast.find_all(node_types):
            raise TemplateSecurityError(
                _("%(node)s requires TemplateFeature.%(feature)s, not enabled for this field")
                % {"node": type(node).__name__, "feature": feature.name}
            )


@functools.lru_cache(maxsize=256)
def _compile(source, delimiters, policy, undefined):
    if policy.max_source_length is not None and len(source) > policy.max_source_length:
        raise TemplateSecurityError(
            _("template source is %(length)s chars, over the %(limit)s limit")
            % {"length": len(source), "limit": policy.max_source_length}
        )
    env = _build_environment(delimiters, policy, undefined)
    ast = env.parse(source)
    _check_policy(ast, policy)
    if policy.max_loop_iterations is not None:
        for loop in ast.find_all(nodes.For):
            loop.iter = nodes.Call(
                nodes.EnvironmentAttribute("count_loop"), [nodes.ContextReference(), loop.iter], [], None, None
            )
    return env.from_string(ast)


def validate_syntax(source, *, delimiters, policy, undefined):
    """Parses `source` and checks it against `policy`, without rendering (no context needed) -
    lets a field reject a syntactically invalid or policy-violating template at `full_clean()`
    time instead of only discovering it at the first `.render()` call. Raises
    `TemplateSecurityError` (or `jinja2.TemplateSyntaxError` for a plain parse error)."""
    _compile(source, delimiters, policy, undefined)


def render(source, *, delimiters, policy, context, undefined):
    """`source` is the field's raw stored text, `context` the dict `available(obj, request=...)`
    built. Enforces `max_render_length` by streaming via `Template.generate()` rather than
    `.render()`, so a runaway loop/recursive macro aborts as soon as it crosses the limit instead
    of building the whole (potentially huge) string first."""
    template = _compile(source, delimiters, policy, undefined)
    context = {**context, _LOOP_COUNT: [0]}
    if policy.max_render_length is None:
        return template.render(context)
    chunks = []
    total = 0
    for chunk in template.generate(context):
        chunks.append(chunk)
        total += len(chunk)
        if total > policy.max_render_length:
            raise TemplateSecurityError(
                _("rendered output exceeded the %(limit)s char limit") % {"limit": policy.max_render_length}
            )
    return "".join(chunks)
