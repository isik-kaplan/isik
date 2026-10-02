"""
Views exercising IdempotencyMixin, served through `urls.py` - through Django's own handler, so
ATOMIC_REQUESTS wraps them exactly as it would in a project.
"""

import threading
from types import SimpleNamespace

from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import CreateAPIView
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from isik.django.apps.idempotency.drf import IdempotencyMixin
from isik.django.apps.idempotency.fingerprint import canonical_body
from tests.testapp.models import Widget


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]


class WidgetNameSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["name"]


class OwnsTheWidget(BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.owner_id == request.user.pk


def without_nonce(request):
    return {key: value for key, value in canonical_body(request).items() if key != "nonce"}


class Gate:
    """Holds a request inside its handler - and so inside its transaction - until released."""

    def __init__(self):
        self.entered = threading.Event()
        self.released = threading.Event()
        self.explode = False

    def hold(self):
        self.entered.set()
        assert self.released.wait(10)
        if self.explode:
            self.explode = False
            raise RuntimeError("the first attempt crashed")


gate = Gate()


class WidgetViewSet(IdempotencyMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    permission_classes = [IsAuthenticated, OwnsTheWidget]
    idempotency_claim_model = "idempotency_by_reference.IdempotencyClaim"
    idempotency_exempt_actions = {"preview": "validates a widget and changes nothing"}
    idempotency_no_replay_actions = {"issue_secret": "the secret is shown once and never stored"}

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["post"])
    def bump(self, request, pk=None):
        widget = self.get_object()
        widget.count += 1
        widget.save()
        return Response(self.get_serializer(widget).data)

    @action(detail=False, methods=["post"])
    def preview(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)

    @action(detail=False, methods=["post"])
    def issue_secret(self, request):
        widget = Widget.objects.create(name="secret-holder", owner=request.user)
        return Response({"secret": f"s3cret-{widget.pk}"}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def refuse(self, request):
        # A refusal returned rather than raised - after doing some work the handler means to keep.
        Widget.objects.create(name="kept", owner=request.user)
        return Response({"detail": "refused"}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"])
    def explode(self, request):
        Widget.objects.create(name="rolled-back", owner=request.user)
        raise ValidationError({"name": ["exploded"]})

    @action(detail=False, methods=["post"])
    def touch(self, request):
        Widget.objects.create(name="touched", owner=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"])
    def plain_dict(self, request):
        Widget.objects.create(name="plain", owner=request.user)
        return Response({"ok": True})

    @action(detail=False, methods=["post"])
    def other_serializer(self, request):
        widget = Widget.objects.create(name="other", owner=request.user)
        return Response(WidgetNameSerializer(widget).data)

    @action(detail=False, methods=["post"])
    def not_a_row(self, request):
        Widget.objects.create(name="not-a-row", owner=request.user)
        return Response(WidgetSerializer(SimpleNamespace(id=1, name="fake", count=0)).data)

    @action(detail=False, methods=["post"], idempotency_normalize=without_nonce)
    def with_nonce(self, request):
        serializer = self.get_serializer(data={"name": request.data["name"]})
        serializer.is_valid(raise_exception=True)
        serializer.save(owner=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def slow(self, request):
        widget = Widget.objects.create(name=request.data["name"], owner=request.user)
        gate.hold()
        return Response(self.get_serializer(widget).data, status=status.HTTP_201_CREATED)


class WidgetBodyViewSet(IdempotencyMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    idempotency_claim_model = "idempotency_with_body.IdempotencyClaimWithBody"
    idempotency_no_replay_actions = {"issue_secret": "the secret is shown once and never stored"}

    @action(detail=False, methods=["post"])
    def issue_secret(self, request):
        widget = Widget.objects.create(name="secret-holder")
        return Response({"secret": f"s3cret-{widget.pk}"}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def plain_dict(self, request):
        widget = Widget.objects.create(name="plain")
        return Response({"made": widget.pk})


class NonceBlindWidgetViewSet(IdempotencyMixin, ModelViewSet):
    """Its normalizer set on the class, as a plain function of the request."""

    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    idempotency_claim_model = "idempotency_by_reference.IdempotencyClaim"
    idempotency_normalize = without_nonce


class OptionalKeyWidgetViewSet(IdempotencyMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    idempotency_claim_model = "idempotency_by_reference.IdempotencyClaim"
    idempotency_key_required = False


class CreateWidgetView(IdempotencyMixin, CreateAPIView):
    """Not a viewset - so there's no action to look up."""

    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    idempotency_claim_model = "idempotency_by_reference.IdempotencyClaim"
