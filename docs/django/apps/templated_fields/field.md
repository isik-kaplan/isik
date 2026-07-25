# field

`TemplateCharField`/`TemplateTextField` store a Jinja template as plain text and render it on demand against a context your own `available(obj, request=None) -> dict` builds - only the keys it returns are ever reachable from the template. Reading the field back gives a `TemplateString` (a `str` subclass bound to the instance); `.render()` does the actual work.

```python
from isik.django.apps.templated_fields import TemplateTextField

def default_text_context(obj, request=None):
    return {"title": obj.title, "me": getattr(request, "me", None)}

class Post(models.Model):
    title = models.CharField(max_length=100)
    default_text = TemplateTextField(available=default_text_context, blank=True)

post = Post.objects.create(title="hello", default_text="hi {{ title }}")
post.default_text                        # "hi {{ title }}" (raw, still a plain str)
post.default_text.render()               # "hi hello"
post.default_text.render(request=request)  # "me" resolves via getattr(request, "me", None)
```

- `available` must be a plain module-level function, not a lambda/closure - like any callable passed to a field kwarg, it has to survive migration serialization as a dotted import path.
- `policy` (default `TemplatePolicy.STANDARD()`) and `delimiters` each resolve through their own settings fallback (`TEMPLATE_FIELDS_POLICY`/`TEMPLATE_FIELDS_DELIMITERS`); `undefined` (`"strict"` default, or `"blank"`, via `TEMPLATE_FIELDS_UNDEFINED`) controls whether a name `available()` didn't provide raises `jinja2.UndefinedError` or silently renders empty.
- `full_clean()`/`Field.clean()` run the template through `engine.validate_syntax()` - a syntactically invalid or policy-violating template fails validation at save time, not first render.
