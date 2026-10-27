"""AutoSchema and enum_name_overrides() - see each one's own docstring."""

import inspect
import re
import weakref
from collections.abc import Mapping
from enum import Enum
from itertools import zip_longest

from django import forms
from django.db.models import Choices
from django_filters import ChoiceFilter
from drf_spectacular.openapi import AutoSchema as _BaseAutoSchema
from drf_spectacular.plumbing import deep_import_string, list_hash
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse
from rest_framework import serializers

from isik._internal.translation import lazy_format
from isik.common.utils.strings import snake_to_pascal
from isik.django.apps.common.db.concurrency import concurrency_field_name
from isik.django.apps.common.generated import generated_models
from isik.django.apps.idempotency.drf import IdempotencyMixin
from isik.django.apps.idempotency.exceptions import (
    IdempotencyKeyInFlight,
    IdempotencyKeyNotReplayable,
    IdempotencyKeyReused,
)
from isik.django.drf.serializers.conditional_serializer import ConditionalSerializerMixin
from isik.django.drf.viewsets.preconditions import (
    PreconditionFailed,
    PreconditionMixin,
    PreconditionRequired,
    requires_precondition,
)


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
    parameters = [
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
    ]
    # Nothing to omit here rather than an enum-less (so effectively free-text) include= - there's
    # no normally-absent field this serializer could ever nest in, so the parameter itself would
    # be a lie, not just an incomplete one.
    if relational_fields:
        parameters.append(
            OpenApiParameter(
                name="include",
                type=OpenApiTypes.STR,
                many=True,
                enum=sorted(relational_fields),
                description="Nest these normally-absent relational fields into the response.",
            )
        )
    return parameters


_ETAG = OpenApiParameter(
    name="ETag",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    response=[200, 201, 304, 412],
    description="The version of the object this response shows. Send it back as it is, in If-Match.",
)


def _if_match(required):
    # type= isn't passed: OpenApiParameter's own default is a string already.
    return OpenApiParameter(
        name="If-Match",
        location=OpenApiParameter.HEADER,
        required=required,
        description="The ETag of the version this request was made against, or * for whatever is current.",
    )


_IF_NONE_MATCH = OpenApiParameter(
    name="If-None-Match",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    description="The ETag of the version already held - a 304 with no body if it's still current.",
)


def _code_field(*codes):
    # A string naming its values rather than an enum: drf-spectacular lifts every enum into a
    # component named after its field, and the same `code` in every resource's 412 - or a project's
    # own `code` field - collides with it, a warning that --fail-on-warn fails on.
    if len(codes) == 1:
        return serializers.CharField(help_text=lazy_format("Always %(code)s.", code=codes[0]))
    return serializers.CharField(help_text=lazy_format("One of %(codes)s.", codes=", ".join(codes)))


def _idempotency_key(view):
    return OpenApiParameter(
        name=view.idempotency_header,
        type=OpenApiTypes.UUID,
        location=OpenApiParameter.HEADER,
        required=view.idempotency_key_required,
        description=(
            "A UUID the client makes for this request and sends again on each retry of it - a retry answers "
            "with the first request's response rather than doing the work twice."
        ),
    )


# {schema generation's component registry: {component name: serializer}} - the error bodies,
# built once per generation, since two classes of one name are a collision. Keyed by the registry
# rather than kept for the process, so nothing one generation built leaks into the next.
_error_serializers = weakref.WeakKeyDictionary()


# What a view's schema_actions doesn't say, so the operation is described as it would be without it.
_UNDECLARED = object()


class AutoSchema(_BaseAutoSchema):
    """
    drf-spectacular `AutoSchema` fixing two gaps that are properties of isik's own mixins, not of
    any one consumer's viewsets - wire it in once, project-wide:

        REST_FRAMEWORK = {"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}

    A project with its own `AutoSchema` customizations subclasses this instead of drf-spectacular's.

    A view's `schema_actions` - `{action: {"request": ..., "response": ..., "status": 201, "parameters":
    [...]}}`, or the same per method, `{action: {"PUT": {...}, "DELETE": {...}}}` - describes its actions where
    `get_serializer_class()` can't, as `@extend_schema` would; read when the schema is generated, so
    it holds whatever `DEFAULT_SCHEMA_CLASS` was when the view was defined.

    An action a view names in `schema_list_actions` publishes a paginated array for its GET, without the
    view's own filters - a page of rows that aren't the view's, as `CommentActions`' comments are.

    `HistoryMixin`'s `history()`/`history_list()` publish a paginated array (drf-spectacular's own
    action-name heuristic doesn't know that, since neither is called `list`), a unique operation id
    (the two paths tokenize identically once the path parameter is dropped, since dropping it is
    what makes both endpoints referring to the same kind of thing describable at all), and their
    built-in filters as query parameters (`history_filterset_class` is applied inside the action
    rather than exposed as `filterset_class`, so drf-spectacular's own `django-filter` introspection
    never sees it) - without also advertising the tracked model's own `filter_backends`-derived
    filters (e.g. `is_active`, `created_at__gte`), which passing to either history endpoint does
    nothing, since neither ever calls `filter_queryset()`.

    Any serializer using `ConditionalSerializerMixin` gets `only=`/`exclude=` documented as query
    parameters - read straight off the query string inside `get_fields()`, so there's nothing for
    schema introspection to find on its own. `include=` joins them too, but only when the
    serializer actually declares `Meta.relational_fields` - its enum comes from there, so it can't
    drift out of sync with what the mixin accepts, and it's omitted entirely rather than
    published as an always-empty, effectively free-text parameter when there's nothing to nest.

    A `PreconditionMixin` view's writes document the `If-Match` header they require and their 412
    (with `current`) and 428 responses, its `retrieve` documents `If-None-Match` and the 304, and
    both, with its create, the `ETag` response header - so generated clients know all of them.

    An `IdempotencyMixin` view's covered operations document the `Idempotency-Key` header (required
    when `idempotency_key_required`), its 422 (the key spent on another request) and its 409 (the
    first request still being served, or - for an action in `idempotency_no_replay_actions` - its
    response not to be sent again). An exempt action documents none of it.
    """

    def _is_list_view(self, serializer=None):
        if getattr(self.view, "action", None) in _HISTORY_ACTIONS or self._is_own_list():
            return True
        return super()._is_list_view(serializer)

    def _is_own_list(self):
        """A GET of an action the view names in `schema_list_actions` - one answering with a page of
        rows that aren't the view's own, as `CommentActions`' comments do."""
        listed = getattr(self.view, "schema_list_actions", ())
        return getattr(self.view, "action", None) in listed and self.method == "GET"

    def get_operation_id(self):
        action = getattr(self.view, "action", None)
        if action in _HISTORY_ACTIONS:
            tokens = [token for token in self._tokenize_path() if token != "history"]
            return f"{'_'.join(tokens) or 'root'}_{action}"
        return super().get_operation_id()

    def get_filter_backends(self):
        # Making _is_list_view() true for these actions (above) has a side effect: the base
        # implementation returns self.view.filter_backends for any list view, and that's the
        # tracked model's own filters (e.g. is_active, created_at__gte) - properties of the
        # viewset's other actions, not of history()/history_list(), which never call
        # filter_queryset()/use filter_backends at all. history_filterset_class's own filters are
        # already added separately in get_override_parameters().
        if getattr(self.view, "action", None) in _HISTORY_ACTIONS or self._is_own_list():
            return []
        return super().get_filter_backends()

    def _declared(self, which):
        """What the view's `schema_actions` says of `which` - "request", "response", "status" or "parameters" - for
        this operation: `{action: {which: value}}`, or `{action: {method: {which: value}}}` per method."""
        declared = getattr(self.view, "schema_actions", {}).get(getattr(self.view, "action", None), {})
        declared = declared.get(self.method, declared)
        return declared.get(which, _UNDECLARED)

    def get_request_serializer(self):
        declared = self._declared("request")
        return super().get_request_serializer() if declared is _UNDECLARED else declared

    def get_response_serializers(self):
        declared = self._declared("response")
        answered = super().get_response_serializers() if declared is _UNDECLARED else declared
        status = self._declared("status")
        return answered if status is _UNDECLARED else {status: answered}

    def get_override_parameters(self):
        parameters = super().get_override_parameters()
        declared = self._declared("parameters")
        if declared is not _UNDECLARED:
            parameters = [*parameters, *declared]
        if getattr(self.view, "action", None) in _HISTORY_ACTIONS:
            parameters = [*parameters, *_history_filter_parameters(self.view.history_filterset_class)]
        serializer_class = self._resolve_serializer_class()
        if serializer_class is not None and issubclass(serializer_class, ConditionalSerializerMixin):
            parameters = [*parameters, *_conditional_serializer_parameters(serializer_class)]
        return [*parameters, *self._precondition_parameters(), *self._idempotency_parameters()]

    def _is_idempotent(self):
        view = self.view
        if not isinstance(view, IdempotencyMixin) or self.method not in view.idempotent_methods:
            return False
        return view.idempotency_action not in view.idempotency_exempt_actions

    def _idempotency_parameters(self):
        return [_idempotency_key(self.view)] if self._is_idempotent() else []

    def _idempotency_responses(self):
        if not self._is_idempotent():
            return {}
        conflicts = [IdempotencyKeyInFlight.default_code]
        name = "IdempotencyKeyInFlight"
        if self.view.idempotency_action in self.view.idempotency_no_replay_actions:
            conflicts.append(IdempotencyKeyNotReplayable.default_code)
            name = "IdempotencyKeyConflict"
        bodies = {
            "409": self._error_serializer(name, {"detail": serializers.CharField(), "code": _code_field(*conflicts)}),
            "422": self._error_serializer(
                "IdempotencyKeyReused",
                {"detail": serializers.CharField(), "code": _code_field(IdempotencyKeyReused.default_code)},
            ),
        }
        return {code: self._get_response_for_code(body, code) for code, body in bodies.items()}

    def _precondition(self):
        """What `PreconditionMixin` does on this operation: "write", "read", "create", or None."""
        view = self.view
        if not isinstance(view, PreconditionMixin):
            return None
        # A model without a concurrency token sends no ETag and compares none - every write exempt.
        model = view.get_precondition_model()
        if model is None or concurrency_field_name(model) is None:
            return None
        action = view.precondition_action
        if requires_precondition(view, self.method, action, getattr(view, "detail", None)):
            return "write"
        if self.method == "GET" and action in view.precondition_read_actions:
            return "read"
        if self._is_create_operation():
            return "create"
        return None

    def _precondition_parameters(self):
        precondition, view = self._precondition(), self.view
        if precondition == "write":
            return [_if_match(view.precondition_option("required")), _ETAG]
        if precondition == "read" and view.precondition_option("not_modified"):
            return [_IF_NONE_MATCH, _ETAG]
        if precondition in ("read", "create"):
            return [_ETAG]
        return []

    def get_operation(self, path, path_regex, path_prefix, method, registry):
        operation = super().get_operation(path, path_regex, path_prefix, method, registry)
        # None for an operation left out of the schema (extend_schema(exclude=True)).
        if operation is not None:
            operation["responses"].update(self._precondition_responses())
            operation["responses"].update(self._idempotency_responses())
        return operation

    def _precondition_responses(self):
        precondition, view = self._precondition(), self.view
        if precondition == "write":
            nullable = view.precondition_option("missing_row_status") == PreconditionFailed.status_code
            bodies = {"412": self._precondition_failed(nullable)}
            # A 428 answers a write without If-Match when it's required, and If-Match: * when that's refused.
            if view.precondition_option("required") or not view.precondition_option("allows_any"):
                bodies["428"] = self._precondition_required()
            return {code: self._get_response_for_code(body, code) for code, body in bodies.items()}
        if precondition == "read" and view.precondition_option("not_modified"):
            not_modified = OpenApiResponse(description="The version If-None-Match named is still current.")
            return {"304": self._get_response_for_code(not_modified, "304")}
        return {}

    def _error_serializer(self, name, fields):
        built = _error_serializers.setdefault(self.registry, {})
        if name not in built:
            built[name] = type(name, (serializers.Serializer,), fields)
        return built[name]

    def _precondition_failed(self, nullable):
        """A 412's body, `current` shown as `retrieve` shows it - and null too, when a missing row is a 412."""
        retrieve = self.view.get_current_serializer_class()
        name = f"{retrieve.__name__.removesuffix('Serializer')}PreconditionFailed{'OrGone' if nullable else ''}"
        fields = {
            "detail": serializers.CharField(),
            "code": _code_field(PreconditionFailed.default_code),
            "current": retrieve(allow_null=nullable),
            "__doc__": "A 412's body: the object as it is now, in `current`.",
        }
        return self._error_serializer(name, fields)

    def _precondition_required(self):
        """A 428's body."""
        fields = {"detail": serializers.CharField(), "code": _code_field(PreconditionRequired.default_code)}
        return self._error_serializer("PreconditionRequired", fields)

    def _resolve_serializer_class(self):
        get_serializer_class = getattr(self.view, "get_serializer_class", None)
        if get_serializer_class is None:
            return None
        try:
            # get_serializer_class() is a normal DRF hook, not one every view can call safely at
            # schema-generation time (e.g. GenericAPIView's own default raises AssertionError if
            # serializer_class was never set, expecting a subclass to override the method instead)
            # - drf-spectacular's own _get_serializer() wraps the equivalent call the same way,
            # skipping just this one view with a warning rather than failing the whole document.
            return get_serializer_class()
        except Exception:
            return None


def _words(name):
    return re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", name)


def _common_name(models):
    """The CamelCase words the names of `models` end with in common - `Vote` of `PostVotesVote` and
    `CommentVotesVote` - else the first one's name."""
    common = []
    # zip_longest, so a shorter name runs out into a None no word equals.
    for words in zip_longest(*(reversed(_words(model.__name__)) for model in models)):
        if len(set(words)) != 1:
            break
        common.insert(0, words[0])
    return "".join(common) or models[0].__name__


def _choices_key(choices):
    """
    The hash drf-spectacular files an override's choices under - read the way it reads them: an
    import string, a `Choices` or `Enum` class, a callable, or the choices themselves, nested groups
    flattened, plain values paired with themselves, and the blank and null values left out.
    """
    if isinstance(choices, str):
        choices = deep_import_string(choices)
    if inspect.isclass(choices) and issubclass(choices, Choices):
        choices = choices.choices
    if inspect.isclass(choices) and issubclass(choices, Enum):
        choices = [(member.value, member.name) for member in choices]
    if callable(choices):
        choices = choices()
    flat = []
    for choice in choices:
        if isinstance(choice, str) or choice is None:
            flat.append((choice, choice))
        elif isinstance(choice[1], list | tuple):
            flat.extend(choice[1])
        else:
            flat.append(choice)
    return list_hash([(value, label) for value, label in flat if value not in ("", None)])


class _EnumNameOverrides(Mapping):
    """`enum_name_overrides()`'s mapping - worked out each time it's read, never when it's made."""

    def __init__(self, own):
        self.own = dict(own)

    def _generated(self):
        """{choices hash: (name, choices)} - one entry per distinct choice set of isik's generated models."""
        groups = {}
        for model in generated_models():
            for field in model._meta.fields:
                if field.choices:
                    group = groups.setdefault(_choices_key(field.choices), (field.name, field.choices, []))
                    group[2].append(model)
        return {
            key: (f"{_common_name(models)}{snake_to_pascal(field_name)}Enum", choices)
            for key, (field_name, choices, models) in groups.items()
        }

    def _resolved(self):
        taken = {_choices_key(choices) for choices in self.own.values()}
        ours = {
            name: choices
            for key, (name, choices) in self._generated().items()
            if key not in taken and name not in self.own
        }
        return {**ours, **self.own}

    def __getitem__(self, name):
        return self._resolved()[name]

    def __iter__(self):
        return iter(self._resolved())

    def __len__(self):
        return len(self._resolved())


def enum_name_overrides(own=None):
    """
    drf-spectacular's `ENUM_NAME_OVERRIDES`, naming the enums of the models isik generates too:

        SPECTACULAR_SETTINGS = {
            "ENUM_NAME_OVERRIDES": enum_name_overrides({"CurrencyEnum": "billing.choices.Currency"}),
        }

    Without it, a generated model's choice field shares its name with a project's own (every vote
    model's `value` and a project's `value`, say), and drf-spectacular warns that it can't name them
    apart - a warning `--fail-on-warn` fails on. Each distinct choice set of the generated models
    (`generated_models()`) gets a name after the words its models' names end with in common and its
    field - `VoteValueEnum` - or after its one model.

    `own` is the project's own overrides, which win: a generated choice set one of them already names,
    or a name one of them takes, is left to it. The mapping is lazy - read when a schema is
    generated, after every model is loaded, not when the settings are.
    """
    return _EnumNameOverrides(own or {})
