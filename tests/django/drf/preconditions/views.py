"""
Views exercising PreconditionMixin, served through `urls.py` - through Django's own handler, so
ATOMIC_REQUESTS wraps them exactly as it would in a project.
"""

import threading

from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.generics import GenericAPIView, ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from isik.django.apps.idempotency.drf import IdempotencyMixin
from isik.django.drf.serializers import ConcurrencyTokenField
from isik.django.drf.viewsets import ActionSerializerClassMixin, PreconditionMixin
from tests.testapp.models import VersionedWidget, Widget


class WidgetSerializer(serializers.ModelSerializer):
    etag = ConcurrencyTokenField()

    class Meta:
        model = Widget
        fields = ["id", "name", "count", "updated_at", "etag"]


class WidgetWriteSerializer(serializers.ModelSerializer):
    """What a write answers with - not what retrieve shows, which a 412's `current` is."""

    etag = ConcurrencyTokenField()

    class Meta:
        model = Widget
        fields = ["id", "name", "count", "etag"]


class VersionedWidgetSerializer(serializers.ModelSerializer):
    etag = ConcurrencyTokenField()

    class Meta:
        model = VersionedWidget
        fields = ["id", "name", "version", "updated_at", "etag"]


class OwnsTheWidget(BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.owner_id == request.user.pk


class ChangesItUnderfoot(BasePermission):
    """Writes the row after get_object() read it and before the lock - another request's write, in effect."""

    def has_object_permission(self, request, view, obj):
        Widget.objects.filter(pk=obj.pk).update(name="changed underfoot")
        return True


class DeletesItUnderfoot(BasePermission):
    """Deletes the row after get_object() read it and before the lock - another request's delete, in effect."""

    def has_object_permission(self, request, view, obj):
        Widget.objects.filter(pk=obj.pk).delete()
        return True


class Gate:
    """Holds a request inside its handler - and so inside its transaction - until released."""

    def __init__(self):
        self.entered = threading.Event()
        self.released = threading.Event()

    def hold(self):
        self.entered.set()
        assert self.released.wait(10)


gate = Gate()


class WidgetViewSet(PreconditionMixin, ActionSerializerClassMixin, ModelViewSet):
    # select_related() makes an outer join, which FOR UPDATE refuses - the lock mustn't take it.
    queryset = Widget.objects.select_related("owner")
    serializer_class = WidgetWriteSerializer
    serializer_class_action_map = {"retrieve": WidgetSerializer, "list": WidgetSerializer}
    permission_classes = [IsAuthenticated, OwnsTheWidget]
    precondition_exempt_actions = {"rename_freely": "a rename that may land over any other write, by design"}

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["post"])
    def bump(self, request, pk=None):
        widget = self.get_object()
        widget.count += 1
        widget.save()
        return Response(self.get_serializer(widget).data)

    @action(detail=True, methods=["post"])
    def rename_freely(self, request, pk=None):
        widget = self.get_object()
        widget.name = request.data["name"]
        widget.save()
        return Response(self.get_serializer(widget).data)

    @action(detail=True, methods=["post"])
    def write_then_check(self, request, pk=None):
        # Work done before the check - a 412 or 428 must roll it back.
        Widget.objects.create(name="written before the check", owner=request.user)
        self.get_object()
        return Response({})

    @action(detail=True, methods=["post"])
    def slow_bump(self, request, pk=None):
        widget = self.get_object()
        gate.hold()
        widget.count += 1
        widget.save()
        return Response(self.get_serializer(widget).data)

    @action(detail=True, methods=["get"])
    def peek(self, request, pk=None):
        return Response({"name": self.get_object().name})

    @action(detail=False, methods=["post"])
    def make_two(self, request):
        Widget.objects.create(name="one", owner=request.user)
        Widget.objects.create(name="two", owner=request.user)
        return Response({})


class UnderfootWidgetViewSet(PreconditionMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    permission_classes = [ChangesItUnderfoot]


class VersionedWidgetViewSet(PreconditionMixin, ModelViewSet):
    queryset = VersionedWidget.objects.all()
    serializer_class = VersionedWidgetSerializer


class WidgetDetailView(PreconditionMixin, RetrieveUpdateDestroyAPIView):
    """Not a viewset - so there's no action to look up."""

    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer


class WidgetListView(PreconditionMixin, ListCreateAPIView):
    """Not a viewset either - its POST is a create all the same."""

    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer


class IdempotentFirstWidgetViewSet(IdempotencyMixin, PreconditionMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    idempotent_methods = ("POST", "PATCH")
    idempotency_claim_model = "idempotency_by_reference.IdempotencyClaim"


class PreconditionFirstWidgetViewSet(PreconditionMixin, IdempotencyMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    idempotent_methods = ("POST", "PATCH")
    idempotency_claim_model = "idempotency_by_reference.IdempotencyClaim"


class StrictWidgetViewSet(PreconditionMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    precondition_ignores_weak_tags = False


class OptionalWidgetViewSet(PreconditionMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    precondition_required = False


class NamedOnlyWidgetViewSet(PreconditionMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    precondition_allows_any = False


class OptionalNamedOnlyWidgetViewSet(PreconditionMixin, ModelViewSet):
    """Options that undo each other - isik.W001's case."""

    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    precondition_required = False
    precondition_allows_any = False


class GoneFailsWidgetViewSet(PreconditionMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    precondition_missing_row_status = 412


class AlwaysInFullWidgetViewSet(PreconditionMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    precondition_not_modified = False


class GoneUnderfootWidgetViewSet(PreconditionMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    permission_classes = [DeletesItUnderfoot]
    precondition_missing_row_status = 412


class PongSerializer(serializers.Serializer):
    pong = serializers.BooleanField()


class PingView(PreconditionMixin, GenericAPIView):
    """A POST that is no create and makes no serializer - nothing to send an ETag for."""

    queryset = Widget.objects.all()
    # For the schema only: post() never makes one.
    serializer_class = PongSerializer

    def post(self, request):
        return Response({"pong": True})
