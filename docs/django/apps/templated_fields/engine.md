# engine

Compiles and renders a template field's Jinja source under its `TemplatePolicy`/`TemplateDelimiters`: builds a `SandboxedEnvironment` restricted to the policy's allowed filters/tests/globals, walks the parsed AST to reject any node type not covered by an enabled `TemplateFeature`, then renders. `_build_environment`/`_compile` are `lru_cache`d since delimiter/policy combos are few and the same template source repeats across renders.

```python
from isik.django.apps.templated_fields.delimiters import TemplateDelimiters
from isik.django.apps.templated_fields.engine import TemplateSecurityError, render, validate_syntax
from isik.django.apps.templated_fields.policy import TemplatePolicy

validate_syntax("{% for x in xs %}{{ x }}{% endfor %}", delimiters=TemplateDelimiters(),
                 policy=TemplatePolicy.STANDARD(), undefined="strict")  # OK, no render context needed

render("hello {{ name }}", delimiters=TemplateDelimiters(), policy=TemplatePolicy.STANDARD(),
       context={"name": "world"}, undefined="strict")  # "hello world"
```

- `render()` streams via `Template.generate()` rather than `.render()`, so `max_render_length` aborts a runaway loop/recursive macro as soon as it crosses the limit instead of building the whole string first.
- `validate_syntax()` raises the same `TemplateSecurityError`/`jinja2.TemplateSyntaxError` a render would, letting a field reject a bad template at `full_clean()` time instead of only at first `.render()`.
