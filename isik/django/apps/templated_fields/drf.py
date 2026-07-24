"""TemplateFieldsModelSerializerMixin - auto-wires TemplateCharField/TemplateTextField model
fields to render {"raw": ..., "rendered": ...} in a ModelSerializer, no per-field declaration
needed. TemplateFieldPreviewMixin adds a viewset action to render an edit-in-progress raw value
without saving it. See each mixin's own docstring."""

import jinja2
from django.core.exceptions import FieldDoesNotExist
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.serializers import ModelSerializer

from isik.django.apps.templated_fields import engine
from isik.django.apps.templated_fields.field import TemplateCharField, TemplateString, TemplateTextField


class TemplateFieldSerializer(serializers.CharField):
    """Reads as `{"raw": <template source>, "rendered": <rendered output>}`; writes take a plain
    string (the new template source), same as an ordinary CharField - the two shapes are
    deliberately asymmetric, matching how the field itself is stored. Write-side also runs the
    template through the model field's own `TemplatePolicy` (via `bind()` picking up the real
    Django field from `Meta.model`) so a syntactically invalid or policy-violating template comes
    back as a normal `serializer.errors` 400 instead of only surfacing later at `.save()`."""

    def bind(self, field_name, parent):
        super().bind(field_name, parent)
        self._model_field = parent.Meta.model._meta.get_field(self.source)

    def run_validation(self, data=serializers.empty):
        value = super().run_validation(data)
        if not value:
            return value
        try:
            engine.validate_syntax(
                value,
                delimiters=self._model_field.delimiters,
                policy=self._model_field.policy,
                undefined=self._model_field.undefined,
            )
        except (engine.TemplateSecurityError, jinja2.TemplateSyntaxError) as e:
            raise serializers.ValidationError(str(e)) from e
        return value

    def to_representation(self, value):
        if not value:
            return {"raw": "", "rendered": ""}
        try:
            rendered = value.render(request=self.context.get("request"))
        except (engine.TemplateSecurityError, jinja2.TemplateSyntaxError, jinja2.UndefinedError):
            # A template valid at write time can still fail at read time - available()'s context
            # shrank, a resource limit that only depends on live data (max_render_length) got
            # tripped, etc. Reading a list of hosts shouldn't 500 because one row's template
            # broke; degrade to a missing rendered value instead.
            rendered = None
        return {"raw": str(value), "rendered": rendered}


class TemplateFieldsModelSerializerMixin:
    """
    Mix in before ModelSerializer/BaseModelSerializer in your bases - MRO reads left to right, and
    this needs to win the `serializer_field_mapping` lookup - to automatically render any
    TemplateCharField/TemplateTextField on the model as `{"raw", "rendered"}`:

        class PostSerializer(TemplateFieldsModelSerializerMixin, BaseModelSerializer):
            class Meta:
                model = Post
                fields = ["id", "default_text"]
    """

    serializer_field_mapping = {
        **ModelSerializer.serializer_field_mapping,
        TemplateCharField: TemplateFieldSerializer,
        TemplateTextField: TemplateFieldSerializer,
    }


class TemplateFieldPreviewMixin:
    """
    Adds `POST /<pk>/preview-template/` to a ModelViewSet - renders an edit-in-progress raw value
    against the existing instance without saving anything, for a live preview while the user is
    still typing:

        POST /api/posts/1/preview-template/
        {"field": "default_text", "raw": "hi {{ title }}"}
        -> 200 {"rendered": "hi hello"}
        -> 400 {"raw": ["..."]}                 if the template is invalid/policy-violating
        -> 400 {"field": ["..."]}                if "field" isn't a template field on this model
    """

    @action(detail=True, methods=["post"], url_path="preview-template")
    def preview_template(self, request, pk=None):
        instance = self.get_object()
        field_name = request.data.get("field")
        try:
            model_field = instance._meta.get_field(field_name)
        except FieldDoesNotExist:
            model_field = None
        if not isinstance(model_field, (TemplateCharField, TemplateTextField)):
            raise serializers.ValidationError({"field": [f"{field_name!r} is not a template field on this model."]})

        raw = request.data.get("raw", "")
        wrapped = TemplateString(
            raw,
            obj=instance,
            available=model_field.available,
            policy=model_field.policy,
            delimiters=model_field.delimiters,
            undefined=model_field.undefined,
        )
        try:
            rendered = wrapped.render(request=request)
        except (engine.TemplateSecurityError, jinja2.TemplateSyntaxError, jinja2.UndefinedError) as e:
            raise serializers.ValidationError({"raw": [str(e)]}) from e
        return Response({"rendered": rendered})
