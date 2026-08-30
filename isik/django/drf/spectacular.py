"""AutoSchema - see its own docstring."""

from django import forms
from django_filters import ChoiceFilter
from drf_spectacular.openapi import AutoSchema as _BaseAutoSchema
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter

from isik.django.drf.serializers.conditional_serializer import ConditionalSerializerMixin


_HISTORY_ACTIONS = ("history", "history_list")


def _openapi_type_for_filter(filter_):
    # ChoiceFilter's field_class is its own ChoiceField, not one of the forms.* checks below, so
    # it has to be caught first rather than falling through to STR by accident.
    if isinstance(filter_, ChoiceFilter):
        return OpenApiTypes.STR
    field_class = filter_.field_class
    # forms.FloatField/DecimalField both subclass forms.IntegerField (Django's own hierarchy, not
    # a numeric one) - checked first so a NumberFilter's real default (DecimalField) doesn't get
    # mistaken for one that's actually integer-typed (e.g. context_field_filter()'s default).
    if issubclass(field_class, forms.DateTimeField):
        return OpenApiTypes.DATETIME
    if issubclass(field_class, (forms.FloatField, forms.DecimalField)):
        return OpenApiTypes.NUMBER
    if issubclass(field_class, forms.IntegerField):
        return OpenApiTypes.INT
    return OpenApiTypes.STR


def _history_filter_parameters(filterset_class):
    # location=/required= aren't passed - OpenApiParameter's own defaults (query, not required)
    # already match every one of these, and passing them again just gives mutmut an equivalent
    # mutant to report (the "removed" kwarg falls back to the exact same default).
    parameters = []
    for name, filter_ in filterset_class.base_filters.items():
        choices = filter_.extra.get("choices")
        parameters.append(
            OpenApiParameter(
                name=name,
                type=_openapi_type_for_filter(filter_),
                enum=[choice[0] for choice in choices] if choices else None,
            )
        )
    return parameters


def _conditional_serializer_parameters(serializer_class):
    meta = getattr(serializer_class, "Meta", None)
    relational_fields = getattr(meta, "relational_fields", None) or {}
    return [
        OpenApiParameter(
            name="only",
            type=OpenApiTypes.STR,
            many=True,
            description="Narrow the response to just these fields (dotted paths reach into nested serializers).",
        ),
        OpenApiParameter(
            name="exclude",
            type=OpenApiTypes.STR,
            many=True,
            description="Drop these fields from the response - applied last, wins over only=/include=.",
        ),
        OpenApiParameter(
            name="include",
            type=OpenApiTypes.STR,
            many=True,
            enum=sorted(relational_fields) or None,
            description="Nest these normally-absent relational fields into the response.",
        ),
    ]


class AutoSchema(_BaseAutoSchema):
    """
    drf-spectacular `AutoSchema` fixing two gaps that are properties of isik's own mixins, not of
    any one consumer's viewsets - wire it in once, project-wide:

        REST_FRAMEWORK = {"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}

    A project with its own `AutoSchema` customizations subclasses this instead of drf-spectacular's.

    `HistoryMixin`'s `history()`/`history_list()` publish a paginated array (drf-spectacular's own
    action-name heuristic doesn't know that, since neither is called `list`), a unique operation id
    (the two paths tokenize identically once the path parameter is dropped, since dropping it is
    what makes both endpoints referring to the same kind of thing describable at all), and their
    built-in filters as query parameters (`history_filterset_class` is applied inside the action
    rather than exposed as `filterset_class`, so drf-spectacular's own `django-filter` introspection
    never sees it).

    Any serializer using `ConditionalSerializerMixin` gets `only=`/`exclude=`/`include=` documented
    as query parameters - read straight off the query string inside `get_fields()`, so there's
    nothing for schema introspection to find on its own. `include=`'s enum comes from
    `Meta.relational_fields`, so it can't drift out of sync with what the mixin actually accepts.
    """

    def _is_list_view(self, serializer=None):
        if getattr(self.view, "action", None) in _HISTORY_ACTIONS:
            return True
        # Passing serializer=None here instead would be unobservable: the base implementation's
        # own fallback (serializer is None -> serializer = self.get_response_serializers())
        # recomputes the exact same value for whatever operation is currently being resolved.
        return super()._is_list_view(serializer)  # pragma: no mutate

    def get_operation_id(self):
        action = getattr(self.view, "action", None)
        if action in _HISTORY_ACTIONS:
            tokens = [token for token in self._tokenize_path() if token != "history"]
            return f"{'_'.join(tokens) or 'root'}_{action}"
        return super().get_operation_id()

    def get_override_parameters(self):
        parameters = super().get_override_parameters()
        if getattr(self.view, "action", None) in _HISTORY_ACTIONS:
            parameters = [*parameters, *_history_filter_parameters(self.view.history_filterset_class)]
        get_serializer_class = getattr(self.view, "get_serializer_class", None)
        serializer_class = get_serializer_class() if get_serializer_class else None
        if serializer_class is not None and issubclass(serializer_class, ConditionalSerializerMixin):
            parameters = [*parameters, *_conditional_serializer_parameters(serializer_class)]
        return parameters
