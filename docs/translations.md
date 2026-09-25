# Translations

Every user-facing string isik produces can be translated: permission messages, validation errors, HTTP exception descriptions, and raised exceptions, including configuration errors. Nothing in isik renders English that your catalogs can't replace.

`isik/locale/isik.pot` lists every message, in the template format translators and `msgmerge` work from.

## With Django

isik looks messages up through Django's own translation machinery, so they come from the catalogs Django already loads: your project's `LOCALE_PATHS`, and each installed app's `locale/` directory. To translate isik's messages, merge the template into your project's catalog and translate the new entries:

```bash
msgmerge --update locale/tr/LC_MESSAGES/django.po path/to/site-packages/isik/locale/isik.pot
# translate the new entries, then:
django-admin compilemessages
```

- Messages stored ahead of time (a permission's `message`, a model field's `verbose_name`) are translated when rendered, so each request gets its active language.
- Messages built on the spot (a raised error, a validation error) are translated when they're built.
- An error raised at import time, before Django's translation machinery is ready, falls back to the untranslated message instead of failing with `AppRegistryNotReady`.

## Without Django

The parts of isik that don't need Django (`isik.common`) use stdlib `gettext` with the `isik` domain. Compile a translated copy of the template per language to `isik.mo`, and point the domain at it:

```python
import gettext

gettext.bindtextdomain("isik", "/path/to/locale")  # expects /path/to/locale/<lang>/LC_MESSAGES/isik.mo
```

## Adding a message to isik

Mark it through `isik._internal.translation`, never with an f-string:

```python
from isik._internal.translation import gettext as _, gettext_lazy, lazy_format

raise ValueError(_("%(name)s is not a field of %(model)s") % {"name": name, "model": model.__name__})
message = gettext_lazy("You may not change this field.")                  # stored, rendered later
message = lazy_format("Actions should not be: %(actions)s", actions=actions)
```

- The msgid has to be a constant string, with `%(name)s` placeholders filled after translating, so catalogs can match it.
- Then regenerate the template with `python -m tests.translation_catalog`.
- `tests/test_translations.py` fails if a user-facing string bypasses translation, if a msgid isn't constant, or if the committed template is out of date.
