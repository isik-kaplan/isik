# templated_fields

Model fields that store a Jinja template and render it on demand against a caller-supplied context, sandboxed via `SandboxedEnvironment` plus an AST-based policy allowlist.

- [field.md](field.md) - `TemplateCharField`/`TemplateTextField`, `TemplateString`
- [engine.md](engine.md) - compiles/renders under a policy, enforces resource limits
- [policy.md](policy.md) - `TemplatePolicy`/`TemplateFeature`, the syntax allowlist
- [delimiters.md](delimiters.md) - `TemplateDelimiters`, customizable `{{ }}`/`{% %}`/`{# #}` syntax
- [drf.md](drf.md) - DRF serializer/viewset mixins (`{"raw", "rendered"}` shape, live preview action)
