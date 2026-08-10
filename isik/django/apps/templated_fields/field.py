"""
TemplateCharField/TemplateTextField - Jinja-templated text fields, restricted to a TemplatePolicy.
`TemplateString.render()` does the actual rendering - see `engine.py` for how.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.fields import DeferredAttribute
from jinja2 import TemplateSyntaxError

from isik.django.apps.templated_fields import engine
from isik.django.apps.templated_fields.delimiters import resolve_delimiters
from isik.django.apps.templated_fields.policy import resolve_policy


def _resolve_undefined(explicit):
    """explicit -> settings.TEMPLATE_FIELDS_UNDEFINED -> "strict"."""
    if explicit is not None:
        return explicit
    return getattr(settings, "TEMPLATE_FIELDS_UNDEFINED", "strict")


class TemplateString(str):
    """
    A field's stored template source, bound to its own model instance. `.render()` calls the
    field's `available(obj, request=None)` to build the render context, then renders under the
    field's own policy/delimiters/undefined setting. A plain `str` otherwise - equality, `len()`,
    etc. all compare against the raw unrendered source, not the rendered output.
    """

    def __new__(cls, value, **_kwargs):
        return super().__new__(cls, value)

    def __init__(self, value, *, obj, available, policy, delimiters, undefined):
        self._obj = obj
        self._available = available
        self._policy = policy
        self._delimiters = delimiters
        self._undefined = undefined

    def render(self, *, request=None, **extra_context):
        context = {**self._available(self._obj, request=request), **extra_context}
        return engine.render(
            str(self),
            delimiters=self._delimiters,
            policy=self._policy,
            context=context,
            undefined=self._undefined,
        )


class TemplateFieldDescriptor(DeferredAttribute):
    def __get__(self, instance, cls=None):
        if instance is None:
            return self
        value = super().__get__(instance, cls)  # pragma: no mutate
        # DeferredAttribute.__get__'s cls param is unused in its own body (only there for the
        # descriptor protocol's signature) - passing a different value here is unobservable.
        return self._wrap(instance, value) if value else value

    def __set__(self, instance, value):
        # DeferredAttribute has no __set__, so without this override a plain attribute assignment
        # (Model.__init__/from_db) would store the raw string directly, bypassing the wrap -
        # TemplateString needs to already be there on the very first read.
        instance.__dict__[self.field.attname] = self._wrap(instance, value)

    def _wrap(self, instance, value):
        field = self.field
        return TemplateString(
            value,
            obj=instance,
            available=field.available,
            policy=field.policy,
            delimiters=field.delimiters,
            undefined=field.undefined,
        )


class _TemplateFieldMixin:
    """Shared __init__/descriptor wiring for TemplateCharField/TemplateTextField - see either for
    the public docstring."""

    descriptor_class = TemplateFieldDescriptor

    def __init__(self, *args, available, policy=None, delimiters=None, undefined=None, **kwargs):
        self.available = available
        self.policy = resolve_policy(policy)
        self.delimiters = resolve_delimiters(delimiters)
        self.undefined = _resolve_undefined(undefined)
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["available"] = self.available
        kwargs["policy"] = self.policy
        kwargs["delimiters"] = self.delimiters
        kwargs["undefined"] = self.undefined
        return name, path, args, kwargs

    def validate(self, value, model_instance):
        super().validate(value, model_instance)  # pragma: no mutate
        # Field.validate()'s model_instance param is unused in its own body (only self.choices/
        # null/blank/value matter there), and this override doesn't use it either.
        if not value:
            return
        try:
            engine.validate_syntax(str(value), delimiters=self.delimiters, policy=self.policy, undefined=self.undefined)
        except (engine.TemplateSecurityError, TemplateSyntaxError) as e:
            raise ValidationError(str(e)) from e


class TemplateCharField(_TemplateFieldMixin, models.CharField):
    """
    A CharField whose stored value is a Jinja template, rendered via `.render()` against a
    context your own `available(obj, request=None) -> dict` builds - only the keys it returns are
    ever reachable from the template, nothing else on `obj`/`request` is:

        def default_text_context(obj, request=None):
            return {"foo": obj.foo, "me": request.me, "talking_to": get_person_url(request)}

        default_text = TemplateCharField(max_length=500, available=default_text_context)

        post.default_text                           # a TemplateString - the raw stored source
        post.default_text.render(request=request)    # the rendered output

    `policy` (a `TemplatePolicy`, default `TemplatePolicy.STANDARD()` or the
    `TEMPLATE_FIELDS_POLICY` setting) controls which Jinja syntax is allowed - see
    `TemplatePolicy`'s own docstring. `delimiters` (a `TemplateDelimiters`, e.g. for `<<var>>`
    instead of `{{ var }}`) resolves the same way via `TEMPLATE_FIELDS_DELIMITERS`. `undefined`
    (`"strict"`, the default, or `"blank"`, also settable via `TEMPLATE_FIELDS_UNDEFINED`)
    controls what happens when the template references a name `available()` didn't provide -
    `"strict"` raises `jinja2.UndefinedError` at render time, `"blank"` silently renders an empty
    string instead.

    `available` must be a plain module-level function, not a lambda or closure - like any other
    callable passed to a model field kwarg (c.f. `default=`), it has to survive migration
    serialization (a dotted import path), which a lambda can't.
    """

    description = "Templated CharField (Jinja, sandboxed + policy-restricted)"


class TemplateTextField(_TemplateFieldMixin, models.TextField):
    """Same as `TemplateCharField` but unbounded, like `TextField` vs `CharField` - see
    `TemplateCharField` for the full docstring."""

    description = "Templated TextField (Jinja, sandboxed + policy-restricted)"
