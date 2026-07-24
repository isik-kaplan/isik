"""
TemplateFeature/TemplatePolicy - the allowlist of Jinja syntax a TemplateCharField/TemplateTextField
permits. See TemplatePolicy's own docstring for the two-layer security model this backs (sandboxed
attribute access vs. this AST-level statement allowlist) - the actual enforcement lives in
`isik.django.apps.templated_fields.engine`, this module only describes what's permitted.
"""

import enum
import functools
from dataclasses import dataclass, field

from django.conf import settings
from jinja2 import nodes


class TemplateFeature(enum.Flag):
    """
    One syntax category a `TemplatePolicy` can permit. Plain `{{ x }}` output and `{# comment #}`
    aren't listed here - Jinja strips comments before parsing (nothing to gate), and a template
    that can't output anything at all isn't a meaningful policy to express, so both are always
    available regardless of policy.
    """

    CONDITIONAL = enum.auto()  # {% if %} / {% elif %} / {% else %}
    FOR_LOOP = enum.auto()  # {% for %}, including `{% for x in y recursive %}`
    LOOP_CONTROLS = enum.auto()  # {% break %} / {% continue %} - needs jinja2.ext.loopcontrols
    MACRO = enum.auto()  # {% macro %} / {% call %}
    SET = enum.auto()  # {% set x = ... %}
    FILTERS = enum.auto()  # {{ x|filter }} - which names are usable is narrowed by allowed_filters
    TESTS = enum.auto()  # {{ x is test }} - narrowed by allowed_tests


# AST node types that require the matching feature. Anything not listed (Const, Compare, CondExpr,
# arithmetic, Output, TemplateData/raw text, Name/Getattr/Getitem) is a plain expression primitive
# that rides along with whatever statement contains it - it doesn't need its own flag.
FEATURE_NODES: dict[TemplateFeature, tuple[type[nodes.Node], ...]] = {
    TemplateFeature.CONDITIONAL: (nodes.If,),
    TemplateFeature.FOR_LOOP: (nodes.For,),
    TemplateFeature.LOOP_CONTROLS: (nodes.Break, nodes.Continue),
    TemplateFeature.MACRO: (nodes.Macro, nodes.CallBlock),
    TemplateFeature.SET: (nodes.Assign, nodes.AssignBlock),
}

# Escape hatches out of the field entirely - never policy-gated, always rejected regardless of
# which features are enabled. There's no legitimate reason a single model field's template needs
# to pull in another template or module.
ALWAYS_BLOCKED_NODES: tuple[type[nodes.Node], ...] = (
    nodes.Include,
    nodes.Import,
    nodes.FromImport,
    nodes.Extends,
)

# Individually dangerous even under an otherwise-permissive filter policy: `safe` suppresses
# autoescaping (reintroduces XSS through field content), `attr` bypasses the normal `.`
# attribute-access path the sandbox reasons about. Stay opt-in - pass them explicitly via
# allowed_filters if you really need one of them.
FILTER_DENYLIST = frozenset({"safe", "attr"})

# Jinja globals capable of conjuring a sizeable value out of thin air rather than from
# available() - `range` is kept but capped via max_loop_iterations (see engine.py) since it's the
# main legitimate use case (`{% for i in range(n) %}`); the rest aren't needed here and are
# dropped rather than individually reasoned about.
DROPPED_GLOBALS = frozenset({"lipsum", "cycler", "joiner", "namespace"})


@dataclass(frozen=True)
class TemplatePolicy:
    """
    Allowlist of Jinja syntax and filters/tests a template field will accept, checked by walking
    the parsed AST before every render. Jinja's own `SandboxedEnvironment` only restricts
    *attribute access* (blocking e.g. `''.__class__.__mro__`), never *which statements are legal*
    - `{% for %}`/`{% macro %}`/`{% include %}` all parse and execute fine under a bare sandboxed
    environment. This is the second, independent layer that actually gates those: a construct is
    only legal if its matching `TemplateFeature` is in `features`. `{% include %}`/`{% import %}`/
    `{% from ... import %}`/`{% extends %}` are always rejected no matter the policy - see
    `ALWAYS_BLOCKED_NODES`.

    `allowed_filters`/`allowed_tests` narrow *which* names are usable once `FILTERS`/`TESTS` is
    turned on - turning the category on doesn't mean all ~50 Jinja builtin filters become
    available. `None` (the default) means every builtin minus `FILTER_DENYLIST` (`safe`, `attr`);
    pass an explicit `frozenset` (e.g. `{"lower", "trim", "default"}`) to lock it down further.

    `max_loop_iterations` caps `range(...)` specifically - the one way template *source* can
    conjure a large iterable without help from `available()`. Iterating a large collection that
    `available()` itself handed over is a different trust boundary (`available()` is your own
    code, not attacker-controlled template text) and isn't capped here. `max_render_length` is a
    blunter backstop against runaway output in general (large loops, deep macro recursion) -
    rendering aborts once accumulated output crosses it. `max_source_length` caps the template
    text itself, checked before parsing.
    """

    features: frozenset[TemplateFeature] = field(default_factory=frozenset)
    allowed_filters: frozenset[str] | None = None
    allowed_tests: frozenset[str] | None = None
    max_source_length: int | None = 10_000
    max_render_length: int | None = 10_000
    max_loop_iterations: int | None = 1_000

    def __post_init__(self):
        object.__setattr__(self, "features", frozenset(self.features))

    def __contains__(self, feature: TemplateFeature) -> bool:
        return feature in self.features

    def deconstruct(self):
        # Hand-written rather than @django.utils.deconstruct.deconstructible: that decorator sets
        # `_constructor_args` on the instance after construction, which a frozen dataclass's own
        # __setattr__ rejects (FrozenInstanceError) - migration serialization only needs
        # hasattr(value, "deconstruct") to be true, not that specific decorator.
        path = f"{type(self).__module__}.{type(self).__qualname__}"
        kwargs = {
            "features": self.features,
            "allowed_filters": self.allowed_filters,
            "allowed_tests": self.allowed_tests,
            "max_source_length": self.max_source_length,
            "max_render_length": self.max_render_length,
            "max_loop_iterations": self.max_loop_iterations,
        }
        return path, [], kwargs

    @classmethod
    @functools.cache
    def VARIABLES_ONLY(cls) -> "TemplatePolicy":  # NOQA
        """Bare `{{ x }}` substitution only - no filters, no tests, no control flow at all."""
        return cls(features=[])

    @classmethod
    @functools.cache
    def STANDARD(cls) -> "TemplatePolicy":  # NOQA
        """+ filters, tests, `{% if %}`/`{% for %}` - the common case for an author-facing
        template written by someone who isn't a developer."""
        return cls(
            features=[
                TemplateFeature.FILTERS,
                TemplateFeature.TESTS,
                TemplateFeature.CONDITIONAL,
                TemplateFeature.FOR_LOOP,
            ]
        )

    @classmethod
    @functools.cache
    def PERMISSIVE(cls) -> "TemplatePolicy":  # NOQA
        """+ `{% break %}`/`{% continue %}`, `{% macro %}`, `{% set %}` - meant for a trusted
        template author (e.g. an internal admin), not arbitrary end users."""
        return cls(
            features=TemplatePolicy.STANDARD().features
            | {TemplateFeature.LOOP_CONTROLS, TemplateFeature.MACRO, TemplateFeature.SET}
        )


def resolve_policy(explicit):
    """explicit -> settings.TEMPLATE_FIELDS_POLICY -> TemplatePolicy.STANDARD()."""
    if explicit is not None:
        return explicit
    configured = getattr(settings, "TEMPLATE_FIELDS_POLICY", None)
    return configured if configured is not None else TemplatePolicy.STANDARD()
