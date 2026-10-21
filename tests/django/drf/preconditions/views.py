"""
Views exercising PreconditionMixin, served through `urls.py` - through Django's own handler, so
ATOMIC_REQUESTS wraps them exactly as it would in a project.
"""

import threading

from django.utils.functional import SimpleLazyObject
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.generics import GenericAPIView, ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from isik.django.apps.idempotency.drf import IdempotencyMixin
from isik.django.drf.serializers import ConcurrencyTokenField
from isik.django.drf.viewsets import ActionSerializerClassMixin, PreconditionMixin
from tests.testapp.models import CleanedNote, VersionedWidget, Widget


class WidgetSerializer(serializers.ModelSerializer):
    etag = ConcurrencyTokenField()
    # A field that queries, as a many=True relation does - a 412's `current` must be rendered before
    # the transaction is marked for rollback, which refuses every query after it.
    siblings = serializers.SerializerMethodField()

    class Meta:
        model = Widget
        fields = ["id", "name", "count", "updated_at", "etag", "siblings"]

    @extend_schema_field({"type": "array", "items": {"type": "string"}})
    def get_siblings(self, widget):
        return [
            str(pk)
            for pk in Widget.objects.filter(owner_id=widget.owner_id).order_by("name").values_list("pk", flat=True)
        ]


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
    precondition_exempt_actions = {
        "rename_freely": "a rename that may land over any other write, by design",
        "rename_mine_freely": "a rename that may land over any other write, by design",
    }

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["post"])
    def bump(self, request, pk=None):
        widget = self.get_object()
        widget.count += 1
        widget.save()
        return Response(self.get_serializer(widget).data)

    @action(detail=True, methods=["post"])
    def bump_unchecked(self, request, pk=None):
        # A write to one object that never fetches it through get_object().
        Widget.objects.filter(pk=pk).update(count=99)
        return Response({})

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

    @action(detail=False, methods=["get"])
    def mine(self, request):
        # A lazy object, as request.user is - ConcurrencyTokenField reads its class through it.
        return Response(self.get_serializer(SimpleLazyObject(lambda: Widget.objects.get(owner=request.user))).data)

    @action(detail=False, methods=["patch"])
    def rename_mine(self, request):
        # Writes one row without get_object() - so without comparing If-Match.
        Widget.objects.filter(owner=request.user).update(name=request.data["name"])
        return Response({})

    @action(detail=False, methods=["patch"])
    def rename_mine_checked(self, request):
        self.kwargs["pk"] = Widget.objects.get(owner=request.user).pk
        widget = self.get_object()
        widget.name = request.data["name"]
        widget.save()
        return Response(self.get_serializer(widget).data)

    @action(detail=False, methods=["patch"])
    def rename_mine_freely(self, request):
        Widget.objects.filter(owner=request.user).update(name=request.data["name"])
        return Response({})

    @action(detail=False, methods=["patch"])
    def refuse_mine(self, request):
        return Response({"detail": "refused"}, status=400)

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


class CleanedNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CleanedNote
        fields = ["id", "count"]


class ExemptNotesViewSet(PreconditionMixin, ModelViewSet):
    """Over a model with no concurrency token - every write exempt, so nothing compares one."""

    queryset = CleanedNote.objects.all()
    serializer_class = CleanedNoteSerializer
    precondition_exempt_actions = {
        action: "notes are written last-one-wins, and their model keeps no version to compare"
        for action in ("update", "partial_update", "destroy", "touch")
    }

    @action(detail=True, methods=["post"])
    def touch(self, request, pk=None):
        note = self.get_object()
        note.save()
        return Response(self.get_serializer(note).data)


class WidgetPeekView(PreconditionMixin, GenericAPIView):
    """Not a viewset, and its GET makes no serializer - its ETag comes from get_object() alone."""

    queryset = Widget.objects.all()
    serializer_class = PongSerializer

    def get(self, request, pk):
        return Response({"name": self.get_object().name})

    def post(self, request, pk):
        # Not a detail action, so If-Match isn't asked - but the row it fetched is the one it answers for.
        widget = self.get_object()
        widget.save()
        return Response({"name": widget.name})
