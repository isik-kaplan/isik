"""
REST views over the audience and its deliveries - `InboxViewSet`, `SubscriptionViewSet`, `DeviceViewSet`,
`EndpointViewSet` and `DeliveryViewSet` - for a project to route, or ignore:

    router.register("notifications/inbox", InboxViewSet, basename="inbox")
    router.register("notifications/subscriptions", SubscriptionViewSet, basename="subscription")
    router.register("notifications/devices", DeviceViewSet, basename="device")
    router.register("notifications/endpoints", AcmeEndpointViewSet, basename="endpoint")
    router.register("notifications/deliveries", AcmeDeliveryViewSet, basename="delivery")

Each is held to isik's write guards (an `Idempotency-Key`, and `If-Match` on a write to one object), with
the exemptions its own writes earn. The inbox, subscriptions and devices are always the caller's own. Whose
endpoints a caller sees, and so whose deliveries, is the project's to say: set `owner_field` - the
endpoint's field naming its owner - and `owner(request)` when the owner isn't the caller, or override
`get_queryset()` (and `perform_create()`).
"""

import secrets
from functools import cache

from django.core.exceptions import ImproperlyConfigured
from django.db.models import CharField
from django.db.models.functions import Cast
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_field,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import mixins, serializers
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy
from isik.common.utils.exemptions import makes_own_exemptions
from isik.django.apps.common.db.models import touch_rows
from isik.django.apps.common.forcing import force_sources
from isik.django.apps.idempotency.drf import IdempotencyMixin, NoIdempotencyKey, NoReplay
from isik.django.apps.notifications import conf
from isik.django.apps.notifications.audience.helpers import (
    SubscriptionRefused,
    mark_read,
    register_device,
    subscribe,
)
from isik.django.apps.notifications.audience.swapping import (
    get_device_model,
    get_endpoint_kind_model,
    get_endpoint_model,
    get_inbox_item_model,
    get_subscription_model,
)
from isik.django.apps.notifications.choices import Policy
from isik.django.apps.notifications.kinds import kinds, rendered, text_of
from isik.django.apps.notifications.pipeline import RetryRefused, gets, is_forced, retry_delivery
from isik.django.apps.notifications.swapping import (
    get_delivery_attempt_model,
    get_delivery_model,
    get_kind_policy_model,
)
from isik.django.drf.error_handling import refusal
from isik.django.drf.viewsets.base import BaseViewSet
from isik.django.drf.viewsets.preconditions import NoPrecondition, PreconditionMixin
from isik.django.drf.viewsets.schema_generation import none_during_schema_generation


@makes_own_exemptions
def _own_exemptions():
    # isik's own, recorded at this line and left out of a project's listing and budgets.
    return {
        "read": NoPrecondition(reason="marks the caller's own items read, which an item without a version can't name"),
        "read_key": NoIdempotencyKey(reason="marks items read, and a retry finds them read already"),
        "preference": NoIdempotencyKey(
            reason="sets the caller's preference to the state it names, so a retry changes nothing"
        ),
        "forget": NoPrecondition(reason="forgets the caller's own preference, which has no version to name"),
        "forget_key": NoIdempotencyKey(reason="forgets a preference, and a retry finds it forgotten"),
        "secret": NoReplay(reason="answers with a new secret, shown once and never stored to be shown again"),
        "register": NoIdempotencyKey(
            reason="registers the token as the caller's device, and a retry finds it registered already"
        ),
        "forget_device": NoPrecondition(reason="forgets the caller's own device - a sign-out, not an edit to race"),
        "forget_device_key": NoIdempotencyKey(reason="forgets a device, and a retry finds it forgotten"),
        "retry": NoPrecondition(
            reason="retries a delivery, which only an abandoned one may be - its state is the guard"
        ),
    }


_EXEMPT = _own_exemptions()

# How a caller's subscriptions, and an endpoint's kinds, are listed.
BY_KIND = ("kind_id", "variant")


# A kind's name - typed here, for a serializer with no model to type it by.
@extend_schema_field(OpenApiTypes.STR)
class KindField(serializers.SlugRelatedField):
    """A kind, by its name - one of the configured policy model's rows that isn't retired."""

    def __init__(self, **kwargs):
        super().__init__(slug_field="kind", **kwargs)

    def get_queryset(self):
        return get_kind_policy_model()._default_manager.filter(retired_at=None)


def _data(item):
    """What an inbox item's text is filled from: the data its payload carries, if any."""
    return item.payload.get("data") or {}


@cache
def inbox_item_serializer(model):
    """The serializer of an inbox item of `model`."""

    class InboxItemSerializer(serializers.ModelSerializer):
        kind = serializers.CharField(source="kind_id", read_only=True)
        title = serializers.SerializerMethodField()
        message = serializers.SerializerMethodField()

        class Meta:
            fields = [
                "id",
                "kind",
                "variant",
                "title",
                "message",
                "subject_label",
                "subject_id",
                "payload",
                "read_at",
                "created_at",
            ]
            read_only_fields = fields

        def get_title(self, item) -> str | None:
            """The item's heading, in the request's language - its kind's `title` filled from its data."""
            return rendered(text_of(item.kind_id).title, _data(item), item.variant)

        def get_message(self, item) -> str | None:
            """The item as a sentence, in the request's language - its kind's `message` filled from its data."""
            return rendered(text_of(item.kind_id).message, _data(item), item.variant)

    InboxItemSerializer.Meta.model = model
    return InboxItemSerializer


@cache
def subscription_serializer(model):
    """The serializer of a subscription of `model`."""

    class SubscriptionSerializer(serializers.ModelSerializer):
        kind = KindField()
        forced = serializers.SerializerMethodField()
        forced_enabled = serializers.SerializerMethodField()
        forced_reason = serializers.SerializerMethodField()

        class Meta:
            fields = ["id", "kind", "variant", "enabled", "forced", "forced_enabled", "forced_reason"]

        def get_forced(self, subscription) -> bool:
            """Whether staff forced it, so it can't be changed - always False on a model that can't be forced."""
            return is_forced(subscription)

        def get_forced_enabled(self, subscription) -> bool | None:
            """What staff forced it to - on or off - while it's forced; `enabled` stays your own choice."""
            return subscription.forced_enabled if self.get_forced(subscription) else None

        def get_forced_reason(self, subscription) -> str | None:
            """Why it was forced - where its source lets the person read it, else none."""
            return shown_reason(subscription) if self.get_forced(subscription) else None

        def validate(self, data):
            # A mandatory kind is subscribe()'s to refuse, with its code - a 409 the client tells apart.
            policy, variant = data["kind"], data.get("variant")
            if variant is not None and variant not in policy.variants:
                raise ValidationError(
                    {
                        "variant": _("%(kind)s declares no variant %(variant)r.")
                        % {"kind": policy.kind, "variant": variant}
                    }
                )
            return data

    SubscriptionSerializer.Meta.model = model
    return SubscriptionSerializer


class EndpointKindSerializer(serializers.Serializer):
    kind = KindField()
    variant = serializers.CharField(allow_null=True, required=False, default=None)

    def validate(self, data):
        policy, variant = data["kind"], data.get("variant")
        if variant is not None and variant not in policy.variants:
            raise ValidationError(
                {"variant": _("%(kind)s declares no variant %(variant)r.") % {"kind": policy.kind, "variant": variant}}
            )
        return data


def kinds_problem(wanted):
    """Why the endpoint can't want `wanted` together, or None - what the database would refuse, said first."""
    seen = {}
    for each in wanted:
        kind, variant = each["kind"].kind, each.get("variant")
        if variant in seen.get(kind, ()):
            return _("%(kind)s is named twice.") % {"kind": kind}
        seen.setdefault(kind, set()).add(variant)
    for kind, variants in seen.items():
        if None in variants and len(variants) > 1:
            return _("%(kind)s is wanted for every variant and for some - name it once, with no variant, for all.") % {
                "kind": kind
            }
    return None


@cache
def endpoint_serializer(model):
    """The serializer of an endpoint of `model` - its secret shown only when it's new."""

    class EndpointSerializer(serializers.ModelSerializer):
        kinds = EndpointKindSerializer(many=True, required=False)
        secret = serializers.SerializerMethodField()

        class Meta:
            fields = ["id", "url", "is_active", "kinds", "consecutive_failures", "parked_at", "secret", "updated_at"]
            # id and updated_at aren't editable, so they're read-only already.
            read_only_fields = ["consecutive_failures", "parked_at"]

        def get_secret(self, endpoint) -> str | None:
            return getattr(endpoint, "new_secret", None)

        def to_representation(self, endpoint):
            data = super().to_representation(endpoint)
            wanted = get_endpoint_kind_model()._default_manager.filter(endpoint=endpoint).order_by(*BY_KIND)
            data["kinds"] = [
                {"kind": kind, "variant": variant} for kind, variant in wanted.values_list("kind_id", "variant")
            ]
            return data

        def validate_kinds(self, wanted):
            problem = kinds_problem(wanted)
            if problem is not None:
                raise ValidationError(problem)
            return wanted

        def _set_kinds(self, endpoint, wanted):
            get_endpoint_kind_model()._default_manager.filter(endpoint=endpoint).delete()
            for each in wanted:
                get_endpoint_kind_model()._default_manager.create(
                    endpoint=endpoint, kind=each["kind"], variant=each.get("variant")
                )

        def create(self, data):
            wanted = data.pop("kinds", [])
            endpoint = model._default_manager.create(**data, secret=secrets.token_urlsafe())
            endpoint.new_secret = endpoint.secret
            self._set_kinds(endpoint, wanted)
            return endpoint

        def update(self, endpoint, data):
            wanted = data.pop("kinds", None)
            endpoint = super().update(endpoint, data)
            if wanted is not None:
                self._set_kinds(endpoint, wanted)
                # Its kinds are part of the endpoint its version names - a change to them alone moves it too.
                touch_rows(model._default_manager.filter(pk=endpoint.pk))
                endpoint.refresh_from_db()
            return endpoint

    EndpointSerializer.Meta.model = model
    return EndpointSerializer


class SwappedModel:
    """
    A viewset's `model`, read from its getter whenever it's asked for - the model a project swapped in, not
    the one isik ships - so isik's checks and schema generation find it on the class, with no request.
    """

    def __init__(self, getter):
        self.getter = getter

    def __get__(self, instance, owner):
        return self.getter()


# Each viewset's docstring, and each action's, is what drf-spectacular publishes to the project's API callers -
# written for them. What a developer needs is in these comments.


# The caller's inbox items, newest first.
@extend_schema_view(
    list=extend_schema(
        parameters=[OpenApiParameter("unread", OpenApiTypes.BOOL, description="Only the unread notifications.")]
    )
)
class InboxViewSet(IdempotencyMixin, PreconditionMixin, BaseViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin):
    """Your notifications, newest first. Add `?unread=true` for the unread ones alone."""

    model = SwappedModel(get_inbox_item_model)
    permission_classes = [IsAuthenticated]
    precondition_exempt_actions = {"read": _EXEMPT["read"], "read_all": _EXEMPT["read"]}
    idempotency_exempt_actions = {"read": _EXEMPT["read_key"], "read_all": _EXEMPT["read_key"]}

    @none_during_schema_generation
    def get_queryset(self):
        items = get_inbox_item_model()._default_manager.filter(user=self.request.user)
        if self.action == "list" and self.request.query_params.get("unread") in ("true", "1"):
            items = items.filter(read_at__isnull=True)
        return items

    def get_serializer_class(self):
        return inbox_item_serializer(get_inbox_item_model())

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def read(self, request, *args, **kwargs):
        """Marks this notification read."""
        item = self.get_object()
        mark_read(self.get_queryset().filter(pk=item.pk))
        item.refresh_from_db()
        return Response(self.get_serializer(item).data)

    @extend_schema(request=None, responses=inline_serializer("Marked", {"marked": serializers.IntegerField()}))
    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request, *args, **kwargs):
        """Marks all your notifications read, and says how many were unread."""
        return Response({"marked": mark_read(self.get_queryset())})


# The caller's subscriptions - each a deviation from its kind's policy. `POST` sets one through
# `subscribe()`, and `DELETE` forgets it, so the kind's policy decides again. DRF's own create, so
# drf-spectacular reads its body and answer as for any; the schema only names the act.
@extend_schema_view(
    create=extend_schema(
        description="Sets your preference for a kind of notification, or one variant of it - made, or replacing the "
        "one before."
    )
)
class SubscriptionViewSet(
    IdempotencyMixin,
    PreconditionMixin,
    BaseViewSet,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
):
    """
    Your notification preferences. Each one turns a kind of notification - or one variant of it - on or off
    for you; setting one replaces the one before, and deleting one goes back to the kind's default.
    """

    model = SwappedModel(get_subscription_model)
    permission_classes = [IsAuthenticated]
    precondition_exempt_actions = {"destroy": _EXEMPT["forget"]}
    idempotency_exempt_actions = {"create": _EXEMPT["preference"], "destroy": _EXEMPT["forget_key"]}

    @none_during_schema_generation
    def get_queryset(self):
        return get_subscription_model()._default_manager.filter(user=self.request.user).order_by(*BY_KIND)

    def get_serializer_class(self):
        return subscription_serializer(get_subscription_model())

    # The row is written by subscribe(), which sets one or replaces the one before.
    def perform_create(self, serializer):
        data = serializer.validated_data
        try:
            serializer.instance = subscribe(
                self.request.user, data["kind"].kind, data.get("variant"), enabled=data.get("enabled", True)
            )
        except SubscriptionRefused as refused:
            raise refusal(str(refused), code=refused.code) from refused
        except ValueError as error:
            raise ValidationError({"variant": [str(error)]}) from error

    def perform_destroy(self, instance):
        if is_forced(instance):
            raise refusal(
                _("This subscription is forced by staff, so it can't be changed."), code="subscription_forced"
            )
        super().perform_destroy(instance)


def shown_reason(forced):
    """`forced`'s reason, where its force source lets the person it's forced on read it - else None."""
    source = force_sources().get(forced.forced_source)
    return forced.forced_reason if source is not None and source.shows_reason_to_holder else None


def kind_states(user, names):
    """
    {kind: {"may_change", "enabled", "enabled_variants", "forced", "forced_enabled", "forced_variants",
    "forced_reason"}} for `user` - one answer for every view saying what a person gets of a kind, whether they may
    change it and whether it's forced for them, so two lists can't disagree, nor either with the fan-out.
    """
    policies = {
        kind: (policy, variants)
        for kind, policy, variants in get_kind_policy_model()
        ._default_manager.filter(kind__in=names)
        .values_list("kind", "policy", "variants")
    }
    rows = list(get_subscription_model()._default_manager.filter(user=user))
    states = {}
    for name in names:
        policy, variants = policies[name]
        mine = {row.variant: row for row in rows if row.kind_id == name}
        whole = mine.get(None)
        forced = whole if is_forced(whole) else None
        states[name] = {
            "may_change": policy != Policy.MANDATORY and forced is None,
            "enabled": gets(whole, policy),
            "enabled_variants": {variant: gets(mine.get(variant, whole), policy) for variant in variants},
            "forced": forced is not None,
            "forced_enabled": forced.forced_enabled if forced is not None else None,
            "forced_variants": sorted(
                variant for variant, row in mine.items() if variant is not None and is_forced(row)
            ),
            "forced_reason": shown_reason(forced) if forced is not None else None,
        }
    return states


class NoticeKindSerializer(serializers.Serializer):
    """A kind of notification as a person sees it: what it is, what it's sent on, and what they may do about it."""

    kind = serializers.CharField(help_text=gettext_lazy("The kind's name - the key everything else names it by."))
    label = serializers.CharField(allow_null=True, help_text=gettext_lazy("Its name, as a person reads it."))
    description = serializers.CharField(allow_null=True, help_text=gettext_lazy("What it's about."))
    policy = serializers.ChoiceField(choices=Policy.choices, help_text=gettext_lazy("What a person gets by default."))
    variants = serializers.ListField(child=serializers.CharField(), help_text=gettext_lazy("Its variants, if any."))
    channels = serializers.ListField(child=serializers.CharField(), help_text=gettext_lazy("What it's sent on."))
    may_change = serializers.BooleanField(help_text=gettext_lazy("Whether you may turn it on or off."))
    enabled = serializers.BooleanField(
        help_text=gettext_lazy("Whether you get it now, every variant - forced, your own choice, or its policy.")
    )
    enabled_variants = serializers.DictField(
        child=serializers.BooleanField(), help_text=gettext_lazy("Whether you get each variant of it now.")
    )
    forced = serializers.BooleanField(help_text=gettext_lazy("Whether staff forced it for you, every variant."))
    forced_enabled = serializers.BooleanField(
        allow_null=True, help_text=gettext_lazy("What staff forced it to for you, every variant - else none.")
    )
    forced_variants = serializers.ListField(
        child=serializers.CharField(), help_text=gettext_lazy("The variants staff forced for you, one by one.")
    )
    forced_reason = serializers.CharField(
        allow_null=True, help_text=gettext_lazy("Why it was forced, if you may read it.")
    )

    def get_fields(self):
        fields = super().get_fields()
        published = conf.setting("NOTICE_KIND_FIELDS")
        return fields if published is None else {name: field for name, field in fields.items() if name in published}


class NoticeKindViewSet(BaseViewSet):
    """
    Every kind of notification you may get - the ones you can't turn off too - with what each is, what it's sent
    on, and whether you may change it.
    """

    # Which kinds a person sees is KINDS_VISIBLE's to say; the fields published, NOTICE_KIND_FIELDS'.
    permission_classes = [IsAuthenticated]
    lookup_field = "kind"
    lookup_value_regex = "[^/]+"
    pagination_class = None

    model = SwappedModel(get_kind_policy_model)

    def get_queryset(self):
        """The kinds declared and not retired - what a schema types the `{kind}` path by, and `visible()` reads."""
        return get_kind_policy_model()._default_manager.filter(retired_at=None, kind__in=list(kinds()))

    def visible(self):
        declared = kinds()
        active = self.get_queryset().order_by("kind").values_list("kind", "policy", "variants")
        hook = conf.setting("KINDS_VISIBLE")
        shown = None if hook is None else set(hook(self.request))
        rows = [row for row in active if shown is None or row[0] in shown]
        states = kind_states(self.request.user, [name for name, _policy, _variants in rows])
        return {
            name: {
                "kind": name,
                "label": _text(text_of(name).label),
                "description": _text(text_of(name).description),
                "policy": policy,
                "variants": variants,
                "channels": list(declared[name].enabled_channels),
                **states[name],
            }
            for name, policy, variants in rows
        }

    @extend_schema(responses=NoticeKindSerializer(many=True))
    def list(self, request, *args, **kwargs):
        return Response(NoticeKindSerializer(list(self.visible().values()), many=True).data)

    @extend_schema(responses=NoticeKindSerializer)
    def retrieve(self, request, *args, **kwargs):
        """One kind of notification, by its name."""
        shown = self.visible().get(kwargs[self.lookup_field])
        if shown is None:
            raise NotFound()
        return Response(NoticeKindSerializer(shown).data)


def _text(text):
    return str(text) if text is not None else None


@cache
def device_serializer(model):
    """The serializer of a device of `model`."""

    class DeviceSerializer(serializers.ModelSerializer):
        class Meta:
            fields = ["id", "platform", "token", "environment", "app_id", "is_active", "last_seen_at"]
            read_only_fields = ["is_active", "last_seen_at"]
            # A token registered before is registered again - moved to the caller - not refused as taken.
            validators = []

    DeviceSerializer.Meta.model = model
    return DeviceSerializer


# The caller's devices. `POST` registers one through `register_device()` - made, or moved to the caller and
# active again - so an app registers its token at every start, with no key and no version.
@extend_schema_view(
    create=extend_schema(
        description="Registers this app install's push token as yours - made, or, registered before by anyone, "
        "made yours and active again. Send it whenever the app starts or its token changes."
    )
)
class DeviceViewSet(
    IdempotencyMixin,
    PreconditionMixin,
    BaseViewSet,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
):
    """
    Your devices that push notifications go to. Delete one when you sign out on it, so it gets no more. A
    device whose token its push service says is dead is inactive, and gets none.
    """

    model = SwappedModel(get_device_model)
    permission_classes = [IsAuthenticated]
    precondition_exempt_actions = {"destroy": _EXEMPT["forget_device"]}
    idempotency_exempt_actions = {"create": _EXEMPT["register"], "destroy": _EXEMPT["forget_device_key"]}

    @none_during_schema_generation
    def get_queryset(self):
        return get_device_model()._default_manager.filter(user=self.request.user).order_by("pk")

    def get_serializer_class(self):
        return device_serializer(get_device_model())

    def perform_create(self, serializer):
        serializer.instance = register_device(self.request.user, **serializer.validated_data)


class OwnedByCaller:
    """
    Whose endpoints a caller sees: those whose `owner_field` is `owner(request)` - the caller, unless it's
    overridden (`request.user.organization`, say). With no `owner_field`, the view refuses to guess.
    """

    owner_field = None

    def owner(self, request):
        return request.user

    def owned_endpoints(self):
        if self.owner_field is None:
            raise ImproperlyConfigured(
                _(
                    "%(view)s doesn't know whose endpoints a caller sees - set its owner_field, or override "
                    "get_queryset()."
                )
                % {"view": type(self).__name__}
            )
        return get_endpoint_model()._default_manager.filter(**{self.owner_field: self.owner(self.request)})


# Webhook endpoints and the kinds each wants. Subclass it to say whose endpoints a caller sees and who owns a
# new one: `owner_field = "user"` (and `owner()`, for an owner that isn't the caller), or `get_queryset()` and
# `perform_create()` of your own - and set ENDPOINT_OWNER_FIELD to match.
class EndpointViewSet(
    OwnedByCaller,
    IdempotencyMixin,
    PreconditionMixin,
    BaseViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """
    Webhook endpoints, and the kinds of notification each receives. An endpoint's secret is shown once:
    in the response that creates it, and in the one that rotates it.
    """

    model = SwappedModel(get_endpoint_model)
    permission_classes = [IsAuthenticated]
    idempotency_no_replay_actions = {"create": _EXEMPT["secret"], "rotate_secret": _EXEMPT["secret"]}

    @none_during_schema_generation
    def get_queryset(self):
        return self.owned_endpoints().order_by("pk")

    def get_serializer_class(self):
        return endpoint_serializer(get_endpoint_model())

    def _answer(self, endpoint):
        return Response(self.get_serializer(endpoint).data)

    def perform_create(self, serializer):
        """Saves the new endpoint, its `owner_field` given the `owner()` - override for an owner set otherwise."""
        serializer.save(**({self.owner_field: self.owner(self.request)} if self.owner_field else {}))

    @extend_schema(request=None)
    @action(detail=True, methods=["post"], url_path="rotate-secret")
    def rotate_secret(self, request, *args, **kwargs):
        """
        Gives the endpoint a new secret, shown here once. Requests are signed with both, the old and the new,
        until the rotation is finished.
        """
        endpoint = self.get_object()
        endpoint.secret_previous, endpoint.secret = endpoint.secret, secrets.token_urlsafe()
        endpoint.save()
        endpoint.new_secret = endpoint.secret
        return self._answer(endpoint)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"], url_path="finish-rotation")
    def finish_rotation(self, request, *args, **kwargs):
        """Stops signing with the endpoint's old secret."""
        endpoint = self.get_object()
        endpoint.secret_previous = None
        endpoint.save()
        return self._answer(endpoint)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def unpark(self, request, *args, **kwargs):
        """Sends to an endpoint again after it was parked for failing too often."""
        endpoint = self.get_object()
        endpoint.unpark()
        return self._answer(endpoint)


@cache
def delivery_serializer(model, attempt_model):
    """The serializer of a delivery of `model`, with its attempts of `attempt_model`."""

    class DeliveryAttemptSerializer(serializers.ModelSerializer):
        class Meta:
            fields = [
                "attempt_no",
                "status_code",
                "succeeded",
                "error",
                "response",
                "started_at",
                "duration_ms",
                "triggered_by",
            ]
            read_only_fields = fields

    class DeliverySerializer(serializers.ModelSerializer):
        kind = serializers.CharField(source="notice.kind_id", read_only=True)
        variant = serializers.CharField(source="notice.variant", read_only=True)
        attempts = DeliveryAttemptSerializer(many=True, read_only=True)

        class Meta:
            fields = [
                "id",
                "kind",
                "variant",
                "channel",
                "target_key",
                "state",
                "attempt_count",
                "next_attempt_at",
                "sent_at",
                "created_at",
                "attempts",
            ]
            read_only_fields = fields

    DeliveryAttemptSerializer.Meta.model = attempt_model
    DeliverySerializer.Meta.model = model
    return DeliverySerializer


# The webhook deliveries to a caller's endpoints. `retry` sends an abandoned one again (`retry_delivery()`),
# refused with why while its endpoint is parked or gone. Whose endpoints are the caller's is `owner_field`'s
# to say, as on EndpointViewSet, or `get_queryset()`'s.
class DeliveryViewSet(
    OwnedByCaller, IdempotencyMixin, PreconditionMixin, BaseViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin
):
    """The webhook requests sent to your endpoints, newest first, each with its attempts."""

    model = SwappedModel(get_delivery_model)
    permission_classes = [IsAuthenticated]
    precondition_exempt_actions = {"retry": _EXEMPT["retry"]}

    def get_serializer_class(self):
        return delivery_serializer(get_delivery_model(), get_delivery_attempt_model())

    @none_during_schema_generation
    def get_queryset(self):
        endpoints = self.owned_endpoints().annotate(key=Cast("pk", CharField())).values("key")
        return (
            get_delivery_model()
            ._default_manager.filter(webhook__endpoint_id__in=endpoints)
            .select_related("notice")
            .prefetch_related("attempts")
            .order_by("-created_at")
        )

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def retry(self, request, *args, **kwargs):
        """Sends a delivery that was given up on again. Refused, saying why, while its endpoint is parked or gone."""
        delivery = self.get_object()
        try:
            delivery = retry_delivery(delivery)
        except RetryRefused as refused:
            raise ValidationError({"detail": str(refused)}) from refused
        return Response(self.get_serializer(delivery).data)
