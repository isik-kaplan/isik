"""
The one way isik marks a string for translation - every user-facing message, raised errors included.

- `gettext(message)`, imported as `_`: translate now. For anything built at the moment it's shown -
  a raised exception, a validation error. Never raises itself: before Django's translation machinery
  is ready (an error at import time, while classes are being defined) it falls back to stdlib
  gettext, so a helpful error is never replaced by `AppRegistryNotReady`.
- `gettext_lazy(message)`: translate when rendered. For anything stored ahead of time - a permission's
  `message`, a model field's `verbose_name` - so each request sees its own language.
- `lazy_format(template, **params)`: `gettext_lazy` with `%(name)s` placeholders filled in when rendered.
- `translate_text(text)`: translate text isik didn't write (HTTPStatus descriptions) - never for isik's
  own messages, which must stay literal so they can be extracted.

Placeholders are always `%(name)s`, filled after translating - the msgid stays constant so catalogs
can match it. Never pass an f-string: its msgid would change with every value.

Where the translation comes from:

- With Django installed and configured: Django's own catalogs - the project's `LOCALE_PATHS` and every
  installed app's `locale/` directory - so adding isik's msgids to your project's `django.po` is all it
  takes. `isik/locale/isik.pot` lists every one of them.
- Without Django (or before it's ready): stdlib gettext, domain `isik` -
  `gettext.bindtextdomain("isik", "/path/to/locale")` with an `isik.mo` per language.
"""

import gettext as stdlib_gettext


DOMAIN = "isik"


def _django_translation():
    try:
        from django.utils import translation
    except ImportError:
        return None
    return translation


def gettext(message):
    translation = _django_translation()
    if translation is not None:
        from django.core.exceptions import AppRegistryNotReady, ImproperlyConfigured

        try:
            return translation.gettext(message)
        except (AppRegistryNotReady, ImproperlyConfigured):  # settings or apps not ready yet
            pass
    return stdlib_gettext.dgettext(DOMAIN, message)


def translate_text(text):
    """
    Translate text that isn't a literal in isik's source - stdlib's HTTPStatus descriptions, which
    http_exceptions renders. The catalog lists those separately, since no extractor can see them.
    """
    return gettext(text)


def lazy_format(template, **params):
    return _lazy(lambda: gettext(template) % params if params else gettext(template))


def gettext_lazy(message):
    return lazy_format(message)


def _lazy(render):
    try:
        from django.utils.functional import lazy
    except ImportError:
        # No Django, so nothing renders per request - translating now is as late as it gets.
        return render()
    return lazy(render, str)()
