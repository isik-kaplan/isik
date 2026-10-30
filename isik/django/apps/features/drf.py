"""
The features app over REST - opt-in views a project routes as it routes isik's others:

    router.register("features", FeatureViewSet, basename="feature")
    router.register("settings", SettingViewSet, basename="setting")

    GET   features/          the holder's features: whether each is in force, and the grants bringing it
    GET   settings/          every setting: type, choices and limits, value, origin, and whether you may change it
    PATCH settings/{name}/   {"type": ..., "value": ...} - the holder's own value; If-Match the settings' ETag

Which holder a request is about is `HOLDER(request)`; which features and settings it sees, `FEATURES_VISIBLE` /
`SETTINGS_VISIBLE`; who may change a setting, `MAY_CHANGE_SETTING`. A setting's own `request_policies` - a fresh
re-authentication, say - run on its PATCH and refuse as they would anywhere: the same gate as the project's own
write path, never a second door past it. `HasFeature(feature)` is a permission class needing a feature.
"""

import hashlib

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, PolymorphicProxySerializer, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy
from isik.common.utils.exemptions import makes_own_exemptions
from isik.django.apps.common.db.concurrency import concurrency_etag
from isik.django.apps.common.forcing import force_sources
from isik.django.apps.features import conf, declarations
from isik.django.apps.features.guards import checks_feature
from isik.django.apps.features.operations import SettingInvalid, SettingRefused, set_setting
from isik.django.apps.features.resolution import effective, has_feature, in_holder_schema, overrides_of, own_value
from isik.django.apps.features.swapping import get_feature_model, get_grant_model, get_overrides_model
from isik.django.apps.idempotency.drf import IdempotencyMixin, NoIdempotencyKey
from isik.django.drf.error_handling import refusal
from isik.django.drf.viewsets.base import BaseViewSet
from isik.django.drf.viewsets.preconditions import (
    NoPrecondition,
    PreconditionFailed,
    PreconditionMixin,
    PreconditionRequired,
    entity_tags,
)


@makes_own_exemptions
def _own_exemptions():
    # isik's own, recorded at this line and left out of a project's listing and budgets.
    return {
        "setting": NoPrecondition(
            reason="compares If-Match itself, with the holder's settings version - a row that may not exist yet"
        ),
        "setting_key": NoIdempotencyKey(reason="sets the holder's own value to the one named; a retry changes nothing"),
    }


_EXEMPT = _own_exemptions()


def holder_of(request):
    """The holder `request` is about - `HOLDER(request)`, else the requester."""
    hook = conf.setting("HOLDER")
    return hook(request) if hook is not None else request.user


def HasFeature(feature):  # noqa: N802 - it makes a permission class, read as one where it's listed
    """A permission class needing `feature` in force for the request's holder - and marking it checked in code."""
    name = checks_feature(feature)

    class HasFeature(BasePermission):
        message = gettext_lazy("This needs a feature you don't have.")
        code = "feature_missing"

        def has_permission(self, request, view):
            return request.user.is_authenticated and has_feature(holder_of(request), name)

    HasFeature.__qualname__ = HasFeature.__name__ = f"HasFeature_{name}"
    return HasFeature


def _visible(hook_name, request, holder, declared):
    hook = conf.setting(hook_name)
    if hook is not None:
        shown = set(hook(request, holder))
        return [name for name in declared if name in shown]
    return [name for name, item in declared.items() if item.visible_to_holder]


def _text(text):
    return str(text) if text is not None else None


class GrantSerializer(serializers.Serializer):
    starts_at = serializers.DateTimeField(help_text=gettext_lazy("When it comes into force."))
    expires_at = serializers.DateTimeField(allow_null=True, help_text=gettext_lazy("When it ends, or none for never."))
    source = serializers.CharField(allow_null=True, help_text=gettext_lazy("Where it came from, where that's shown."))
    reference = serializers.CharField(allow_null=True, help_text=gettext_lazy("The source's reference, where shown."))


class FeatureSerializer(serializers.Serializer):
    name = serializers.CharField(help_text=gettext_lazy("The feature's name - the key everything names it by."))
    label = serializers.CharField(allow_null=True, help_text=gettext_lazy("Its name, as a person reads it."))
    description = serializers.CharField(allow_null=True, help_text=gettext_lazy("What it gives."))
    held = serializers.BooleanField(help_text=gettext_lazy("Whether it's in force for you now."))
    grants = GrantSerializer(many=True, help_text=gettext_lazy("Its grants in force now or to come."))


class FeatureViewSet(BaseViewSet):
    """Your features: which are in force for you, and the grants that bring them."""

    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        # Never listed from - the answer is built from the declarations - but it names the model to the schema.
        return get_feature_model()._default_manager.none()

    @extend_schema(responses=FeatureSerializer(many=True))
    def list(self, request, *args, **kwargs):
        holder = holder_of(request)
        names = _visible("FEATURES_VISIBLE", request, holder, declarations.features)
        shows_source = conf.setting("SHOW_GRANT_SOURCE")
        with in_holder_schema():
            grants = list(
                get_grant_model()
                ._default_manager.filter(holder=holder, revoked_at__isnull=True)
                .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now()))
                .order_by("starts_at")
            )
        listed = [
            {
                "name": name,
                "label": _text(declarations.features[name].label),
                "description": _text(declarations.features[name].description),
                "held": has_feature(holder, name),
                "grants": [
                    {
                        "starts_at": grant.starts_at,
                        "expires_at": grant.expires_at,
                        "source": grant.source if shows_source else None,
                        "reference": grant.reference if shows_source else None,
                    }
                    for grant in grants
                    if grant.feature_id == name
                ],
            }
            for name in names
        ]
        return Response(FeatureSerializer(listed, many=True).data)


# Each setting type's value, as the schema types it - a discriminated union on "type". Never null: a declared
# setting always has a value, which isik.E017 holds a declaration to.
VALUE_FIELDS = {
    "bool": lambda **kwargs: serializers.BooleanField(**kwargs),
    "int": lambda **kwargs: serializers.IntegerField(**kwargs),
    "decimal": lambda **kwargs: serializers.DecimalField(max_digits=None, decimal_places=None, **kwargs),
    "float": lambda **kwargs: serializers.FloatField(**kwargs),
    "duration": lambda **kwargs: serializers.DurationField(**kwargs),
    "text": lambda **kwargs: serializers.CharField(allow_blank=True, **kwargs),
    "choice": lambda **kwargs: serializers.JSONField(**kwargs),
}


class ChoiceSerializer(serializers.Serializer):
    value = serializers.JSONField(help_text=gettext_lazy("What's stored."))
    label = serializers.CharField(help_text=gettext_lazy("What a person reads."))


# The types a setting's limits are shown for.
NUMBERS = ("int", "decimal", "float")


class SettingFields(serializers.Serializer):
    """What every setting shows, whatever its type - each type's own serializer adds its typed value."""

    name = serializers.CharField(help_text=gettext_lazy("The setting's name - the key it's changed by."))
    # A string, not a one-choice enum: the discriminator's mapping already names each, and seven enums all called
    # "type" would collide in the schema.
    type = serializers.CharField(help_text=gettext_lazy("What kind of value it holds."))
    label = serializers.CharField(allow_null=True, help_text=gettext_lazy("Its name, as a person reads it."))
    description = serializers.CharField(allow_null=True, help_text=gettext_lazy("What it does."))
    origin = serializers.ChoiceField(
        choices=[(origin, origin) for origin in ("forced", "feature_missing", "limited", "own", "default")],
        help_text=gettext_lazy("Why it's this value: forced, for want of a feature, limited, yours, or the default."),
    )
    forced_reason = serializers.CharField(
        allow_null=True, help_text=gettext_lazy("Why it was forced, if you may read it.")
    )
    may_change = serializers.BooleanField(help_text=gettext_lazy("Whether you may change it."))
    requires_feature = serializers.CharField(
        allow_null=True, help_text=gettext_lazy("The feature it needs, where you may see it - else none.")
    )


def _setting_serializer(kind):
    """The serializer of a setting of the type `kind` - its `value` and `default` typed."""
    fields = {
        "value": VALUE_FIELDS[kind](),
        "default": VALUE_FIELDS[kind](),
        # What a release or the feature brings back - beside what's in force.
        "chosen": VALUE_FIELDS[kind](allow_null=True, help_text=gettext_lazy("Your own choice, or none yet.")),
    }
    if kind == "choice":
        fields["choices"] = ChoiceSerializer(many=True, help_text=gettext_lazy("What it may be."))
    if kind in NUMBERS:
        # None where the field has no such validator.
        fields["minimum"] = VALUE_FIELDS[kind](allow_null=True)
        fields["maximum"] = VALUE_FIELDS[kind](allow_null=True)
    name = f"{kind.capitalize()}Setting"
    return type(name, (SettingFields,), {**fields, "__doc__": f"A setting holding a {kind} value."})


SETTING_SERIALIZERS = {kind: _setting_serializer(kind) for kind in VALUE_FIELDS}
SETTING_SCHEMA = PolymorphicProxySerializer(
    component_name="Setting", serializers=SETTING_SERIALIZERS, resource_type_field_name="type", many=False
)


class ValueFields(serializers.Serializer):
    """What a PATCH names, whatever the setting's type - each type's own serializer adds its typed value."""

    type = serializers.CharField(help_text=gettext_lazy("The setting's type, as listed."))


def _value_serializer(kind):
    return type(f"{kind.capitalize()}SettingValue", (ValueFields,), {"value": VALUE_FIELDS[kind]()})


VALUE_SERIALIZERS = {kind: _value_serializer(kind) for kind in VALUE_FIELDS}
VALUE_SCHEMA = PolymorphicProxySerializer(
    component_name="SettingValue", serializers=VALUE_SERIALIZERS, resource_type_field_name="type", many=False
)


class SettingRefusal(serializers.Serializer):
    """A refused change: why, and its code - setting_forced or setting_locked on a 409, a request policy's own on
    a 403."""

    detail = serializers.CharField(help_text=gettext_lazy("Why, in the request's language."))
    code = serializers.CharField(help_text=gettext_lazy("What refused it, for a client to tell refusals apart."))


class SettingsChanged(serializers.Serializer):
    """A 412's body: the settings changed since the version If-Match named - read them again."""

    detail = serializers.CharField(help_text=gettext_lazy("Why, in the request's language."))
    code = serializers.CharField(help_text=gettext_lazy("Always precondition_failed."))
    current = serializers.JSONField(allow_null=True, help_text=gettext_lazy("Always null: read the settings again."))


class SettingInvalidValue(serializers.Serializer):
    """A 400's body: the type named isn't the setting's, or the value isn't one it takes."""

    type = serializers.ListField(child=serializers.CharField(), required=False)
    value = serializers.ListField(child=serializers.CharField(), required=False)


# The header the settings' version is sent in.
ETAG = "ETag"

SETTINGS_VERSION = OpenApiParameter(
    ETAG,
    OpenApiTypes.STR,
    OpenApiParameter.HEADER,
    response=[200],
    description=gettext_lazy("The version of all your settings - send it back as it is, in If-Match."),
)


def _limits(field):
    minimum = next((v.limit_value for v in field.validators if isinstance(v, MinValueValidator)), None)
    maximum = next((v.limit_value for v in field.validators if isinstance(v, MaxValueValidator)), None)
    return minimum, maximum


def defaults_etag():
    """The settings' version while a holder has no overrides row: one per set of declarations and their defaults."""
    declared = sorted((name, repr(setting.default)) for name, setting in declarations.settings.items())
    return f'"defaults-{hashlib.sha256(repr(declared).encode()).hexdigest()[:16]}"'


class SettingViewSet(IdempotencyMixin, PreconditionMixin, BaseViewSet):
    """Your settings: what each is, why it's that value, and whether you may change it."""

    permission_classes = [IsAuthenticated]
    pagination_class = None
    lookup_field = "name"
    lookup_value_regex = "[^/]+"
    precondition_exempt_actions = {"partial_update": _EXEMPT["setting"]}
    idempotency_exempt_actions = {"partial_update": _EXEMPT["setting_key"]}

    def get_queryset(self):
        # As FeatureViewSet's: only for the schema. A setting is named by its declaration, not a row.
        return get_feature_model()._default_manager.none()

    def visible_features(self, holder):
        return _visible("FEATURES_VISIBLE", self.request, holder, declarations.features)

    def visible_names(self, holder):
        return _visible("SETTINGS_VISIBLE", self.request, holder, declarations.settings)

    def may_change(self, holder, name, origin):
        if origin in ("forced", "feature_missing") or not declarations.settings[name].holder_may_change:
            return False
        hook = conf.setting("MAY_CHANGE_SETTING")
        return hook(self.request, holder, name) if hook is not None else holder == self.request.user

    def shown(self, holder, name):
        setting = declarations.settings[name]
        now = effective(holder, name)
        # Only a forced value has a source.
        source = force_sources().get(now.source)
        shown = {
            "name": name,
            "type": setting.type,
            "label": _text(setting.label),
            "description": _text(setting.description),
            "value": now.value,
            "default": setting.default,
            "origin": now.origin,
            "forced_reason": now.reason if source is not None and source.shows_reason_to_holder else None,
            "may_change": self.may_change(holder, name, now.origin),
            "chosen": own_value(holder, setting, overrides_of(holder)),
            "requires_feature": setting.feature if setting.feature in self.visible_features(holder) else None,
        }
        if setting.type == "choice":
            shown["choices"] = [{"value": value, "label": str(label)} for value, label in setting.field.choices]
        if setting.type in NUMBERS:
            shown["minimum"], shown["maximum"] = _limits(setting.field)
        return SETTING_SERIALIZERS[setting.type](shown).data

    def etag(self, holder):
        model = get_overrides_model()
        with in_holder_schema():
            row = model._default_manager.filter(holder=holder).first() if model is not None else None
        return concurrency_etag(row) if row is not None else defaults_etag()

    @extend_schema(
        responses=PolymorphicProxySerializer(
            component_name="Setting", serializers=SETTING_SERIALIZERS, resource_type_field_name="type", many=True
        ),
        parameters=[SETTINGS_VERSION],
    )
    def list(self, request, *args, **kwargs):
        holder = holder_of(request)
        return Response(
            [self.shown(holder, name) for name in self.visible_names(holder)], headers={ETAG: self.etag(holder)}
        )

    @extend_schema(
        request=VALUE_SCHEMA,
        responses={
            200: SETTING_SCHEMA,
            400: SettingInvalidValue,
            403: SettingRefusal,
            404: SettingRefusal,
            409: SettingRefusal,
            412: SettingsChanged,
            428: SettingRefusal,
        },
        parameters=[
            OpenApiParameter(
                "name", OpenApiTypes.STR, OpenApiParameter.PATH, description=gettext_lazy("The setting's name.")
            ),
            OpenApiParameter(
                "If-Match",
                OpenApiTypes.STR,
                OpenApiParameter.HEADER,
                required=True,
                description=gettext_lazy(
                    'The ETag of your settings as you read them - a "defaults" one until your first change - or *.'
                ),
            ),
            SETTINGS_VERSION,
        ],
    )
    def partial_update(self, request, *args, **kwargs):
        """Sets your own value of one setting. Name the settings' version you read, in If-Match."""
        holder, name = holder_of(request), kwargs[self.lookup_field]
        if name not in self.visible_names(holder):
            raise NotFound()
        setting = declarations.settings[name]
        if request.data.get("type") != setting.type:
            raise ValidationError({"type": [_("%(name)s holds a %(type)s.") % {"name": name, "type": setting.type}]})
        if not self.may_change(holder, name, effective(holder, name).origin):
            raise PermissionDenied()
        self.check_setting_policies(setting)
        header = request.META.get("HTTP_IF_MATCH")
        if header is None:
            raise PreconditionRequired()
        with in_holder_schema(), transaction.atomic():
            self.compared(holder, header)
            try:
                set_setting(holder, name, request.data.get("value"))
            except SettingInvalid as invalid:
                raise ValidationError({"value": [str(invalid)]}) from invalid
            except SettingRefused as refused:
                raise refusal(str(refused), code=refused.code) from refused
        return Response(self.shown(holder, name), headers={ETAG: self.etag(holder)})

    def check_setting_policies(self, setting):
        """The setting's own request policies, refused and allowed as the view's own are."""
        for policy_class in setting.request_policies:
            policy = policy_class()
            if not policy.allows(self.request, self):
                self.refused_by = policy
                self.permission_denied(self.request, message=policy.message, code=policy.code)
            self.allowed_by = (*self.allowed_by, policy)

    def compared(self, holder, header):
        """Refuses a write made against another version than the holder's settings now are - creating the row
        when it's the first, and refusing the second of two first writes made at once."""
        model = get_overrides_model()
        row = model._default_manager.select_for_update().filter(holder=holder).first()
        current = concurrency_etag(row) if row is not None else defaults_etag()
        tags = entity_tags(header, ignores_weak=self.precondition_option("ignores_weak_tags"))
        if "*" not in tags and current not in tags:
            raise PreconditionFailed(None)
        if row is None:
            try:
                with transaction.atomic():
                    model._default_manager.create(holder=holder)
            except IntegrityError:
                raise PreconditionFailed(None) from None
