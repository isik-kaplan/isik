"""
Renders a template field's Jinja source under a `TemplatePolicy`/`TemplateDelimiters`: a
`SandboxedEnvironment` for attribute-access safety, plus the AST-level allowlist from `policy.py`
for which statements are legal at all - see `TemplatePolicy`'s docstring for why both layers exist.
`_build_environment`/`_compile` are cached, since delimiters/policy combinations are few and
distinct template sources tend to repeat across renders of the same field value.
"""

import functools

from jinja2 import StrictUndefined, Undefined
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
    env = SandboxedEnvironment(
        autoescape=True,
        extensions=extensions,
        undefined=_UNDEFINED_CLASSES[undefined],
        **delimiters.as_environment_kwargs(),
    )

    for name in DROPPED_GLOBALS:
        env.globals.pop(name, None)
    if TemplateFeature.FOR_LOOP in policy:
        env.globals["range"] = _capped_range(policy.max_loop_iterations)
    else:
        env.globals.pop("range", None)

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
    return env.from_string(source)


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
    if policy.max_render_length is None:
        return template.render(**context)
    chunks = []
    total = 0
    for chunk in template.generate(**context):
        chunks.append(chunk)
        total += len(chunk)
        if total > policy.max_render_length:
            raise TemplateSecurityError(
                _("rendered output exceeded the %(limit)s char limit") % {"limit": policy.max_render_length}
            )
    return "".join(chunks)
