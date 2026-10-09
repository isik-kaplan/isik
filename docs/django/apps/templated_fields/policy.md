# policy

`TemplatePolicy` is the AST-level allowlist of Jinja syntax a `TemplateCharField`/`TemplateTextField` accepts - which `TemplateFeature`s (conditionals, for-loops, macros, set, filters, tests, loop controls) are turned on, plus `allowed_filters`/`allowed_tests` narrowing, and resource caps (`max_source_length`, `max_render_length`, `max_loop_iterations`). Three cached presets cover the common cases; `include`/`import`/`from...import`/`extends` are rejected unconditionally, no matter the policy.

```python
from isik.django.apps.templated_fields.policy import TemplateFeature, TemplatePolicy

TemplatePolicy.VARIABLES_ONLY()  # bare {{ x }} substitution only
TemplatePolicy.STANDARD()        # + filters, tests, {% if %}/{% for %} - default
TemplatePolicy.PERMISSIVE()      # + break/continue, macro, set - trusted authors only

TemplatePolicy(features=[TemplateFeature.FILTERS], allowed_filters=frozenset({"lower", "trim"}))
```

- Two independent security layers: `SandboxedEnvironment` (in `engine.py`) blocks dangerous *attribute access* (e.g. `''.__class__`); `TemplatePolicy` is the separate layer gating which *statements* are legal at all - a bare sandboxed environment happily parses and runs `{% for %}`/`{% macro %}`/`{% include %}`.
- `max_loop_iterations` (default 1000) is a budget for the whole render: every `{% for %}` iteration counts, nested ones multiply and a loop that writes nothing still counts. Each `range()` call is capped at it too.
- `max_render_length` (default 10000) caps the output, and also refuses a `*` or `**` whose result would be longer, before computing it - `{{ "x" * 400000000 }}` fails at once instead of allocating 400MB.
- `allowed_filters=None` (the default) means every Jinja builtin filter minus `FILTER_DENYLIST` (`safe`, `attr`) - turning `FILTERS` on doesn't mean all ~50 builtins are usable without narrowing.
