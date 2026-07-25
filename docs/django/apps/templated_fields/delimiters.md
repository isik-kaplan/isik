# delimiters

`TemplateDelimiters` is the placeholder syntax a template field's Jinja environment looks for (`{{ }}`/`{% %}`/`{# #}` by default). `resolve_delimiters` picks one: explicit kwarg -> `settings.TEMPLATE_FIELDS_DELIMITERS` -> Jinja's own defaults.

```python
from isik.django.apps.templated_fields.delimiters import TemplateDelimiters

custom = TemplateDelimiters(variable_start_string="<<", variable_end_string=">>")
custom.as_environment_kwargs()  # kwargs for jinja2.Environment/SandboxedEnvironment
# "hi <<name>>" renders; "hi {{ name }}" is now inert literal text
```

- `deconstruct()` is hand-written rather than `@django.utils.deconstruct.deconstructible` - that decorator sets `_constructor_args` post-init, which a frozen dataclass's `__setattr__` rejects.
