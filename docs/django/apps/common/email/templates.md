# templates

Two thin wrappers around `render_to_string` for rendering email bodies: `text_template` for plain
text, `mjml_template` for MJML source compiled to HTML via `mjml2html`. Both forward `request` to
`render_to_string` so template context processors fire.

```python
from isik.django.apps.common.email import mjml_template, text_template

text_template("welcome.txt", {"name": "Alice"})
mjml_template("welcome.mjml", {"name": "Alice"}, request)
```

- `mjml_template` compiles with `disable_comments=True` — comments in the MJML source are
  stripped from the output HTML.

## Translating MJML templates

`manage.py makemessages` reads `.mjml` files too: the common app's `makemessages` is Django's, its django domain
reading `html`, `txt`, `py` and `mjml` when no `--extension` names the files. A `{% translate %}` in an MJML
template reaches the catalog like one in an HTML template. `--extension` names the files itself, as in Django's,
and the djangojs domain is Django's own.
