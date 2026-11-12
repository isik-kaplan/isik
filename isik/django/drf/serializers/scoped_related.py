"""ScopedRelatedField - a ScopedForeignKey on the wire."""

from collections.abc import Mapping

from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from rest_framework import serializers
from rest_framework.fields import get_attribute
from rest_framework.relations import PKOnlyObject

from isik._internal.translation import gettext as _


class ScopedRelatedField(serializers.PrimaryKeyRelatedField):
    """
    A `ScopedForeignKey` as its target's id: read from its `<name>_id` column, written as an id looked up in
    `queryset`, within the scopes the request may write to - `scopes(request)`, a scope or several (a queryset
    of them included):

        class ResponseSerializer(serializers.ModelSerializer):
            attempt = ScopedRelatedField(
                scope="campaign",
                queryset=Attempt.objects.all(),
                scopes=lambda request: Campaign.objects.filter(members=request.user),
            )

    The lookup is within the scope the request writes, where the serializer has a writable scope field and the
    request sends it; else within the instance's, on an update; else within `scopes(request)` - a create whose
    scope the server derives, the row taking its scope from the target it names. That one needs `scopes`, or
    any target of any scope would do: without it, such a write is refused as configured wrong. An id outside
    the lookup reads as one that isn't there.
    """

    def __init__(self, *, scope, scopes=None, **kwargs):
        self.scope = scope
        self.scopes = scopes
        super().__init__(**kwargs)

    def _within_scopes(self, queryset, attname):
        if self.scopes is None:
            return queryset
        request = self.context.get("request")
        if request is None:
            raise ImproperlyConfigured(
                _(
                    "%(field)s writes only to the scopes a request may write to - give its serializer the request "
                    "in its context."
                )
                % {"field": self.field_name}
            )
        allowed = self.scopes(request)
        if isinstance(allowed, (models.QuerySet, list, tuple, set, frozenset)):
            return queryset.filter(**{f"{attname}__in": allowed})
        return queryset.filter(**{attname: allowed})

    def get_queryset(self):
        queryset = super().get_queryset()
        field = queryset.model._meta.get_field(self.scope)
        written = self.parent.fields.get(self.scope)
        data = getattr(self.parent, "initial_data", None)
        if written is not None and not written.read_only and isinstance(data, Mapping) and self.scope in data:
            try:
                # A key's to_python is its target's.
                value = field.to_python(data[self.scope])
            except (DjangoValidationError, TypeError, ValueError):
                return queryset.none()
            if value is None:
                return queryset.none()
            return self._within_scopes(queryset.filter(**{field.attname: value}), field.attname)
        instance = self.parent.instance
        if instance is not None:
            return queryset.filter(**{field.attname: getattr(instance, field.attname)})
        if self.scopes is None:
            raise ImproperlyConfigured(
                _(
                    "%(field)s takes its scope from the target a request names - give it scopes=, the scopes a "
                    "request may write to, or any target of any scope would do."
                )
                % {"field": self.field_name}
            )
        return self._within_scopes(queryset, field.attname)

    def get_attribute(self, instance):
        *path, name = self.source_attrs
        value = getattr(get_attribute(instance, path), f"{name}_id")
        # DRF reads a PKOnlyObject with no pk as no value.
        return PKOnlyObject(pk=value)
