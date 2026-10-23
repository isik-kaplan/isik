"""Views pairing - or failing to pair - each write that asks If-Match with a read handing out its version."""

from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.generics import GenericAPIView, RetrieveAPIView, UpdateAPIView
from rest_framework.mixins import RetrieveModelMixin, UpdateModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from isik.django.drf.viewsets import NoReadableVersion, PreconditionMixin
from tests.django.drf.preconditions.views import WidgetSerializer
from tests.testapp.models import CleanedNote, VersionedWidget, Widget


UNREAD = "renamed by a script that holds the version it read from the database"


def unread():
    return NoReadableVersion(reason=UNREAD)


class MeViewSet(PreconditionMixin, ModelViewSet):
    """`me` hands out the version `rename_me` names - a detail=False pair, declared."""

    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    precondition_read_actions = ("retrieve", "me", "me_unchecked")
    precondition_read_from = {"rename_me": "me"}

    def mine(self):
        self.kwargs["pk"] = Widget.objects.get(owner=self.request.user).pk
        return self.get_object()

    @action(detail=False, methods=["get"])
    def me(self, request):
        return Response(self.get_serializer(self.mine()).data)

    @action(detail=False, methods=["get"])
    def me_unchecked(self, request):
        # Named a read, yet never fetches the row through get_object() - so it sends no ETag.
        return Response(self.get_serializer(Widget.objects.get(owner=request.user)).data)

    @action(detail=False, methods=["patch"])
    def rename_me(self, request):
        widget = self.mine()
        widget.name = request.data["name"]
        widget.save()
        return Response(self.get_serializer(widget).data)


class UnroutedReadViewSet(PreconditionMixin, GenericViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    precondition_read_from = {"rename_me": "nowhere"}

    @action(detail=False, methods=["patch"])
    def rename_me(self, request):
        return Response({})


class SilentReadViewSet(PreconditionMixin, GenericViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    precondition_read_from = {"rename_me": "peek"}

    @action(detail=False, methods=["get"])
    def peek(self, request):
        return Response({})

    @action(detail=False, methods=["patch"])
    def rename_me(self, request):
        return Response({})


class UnreadViewSet(PreconditionMixin, GenericViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    precondition_unread_actions = {"rename_me": unread()}

    @action(detail=False, methods=["patch"])
    def rename_me(self, request):
        return Response({})


class NoRetrieveViewSet(PreconditionMixin, GenericViewSet):
    """A write to one object, and no retrieve routed to read its version from."""

    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer

    @action(detail=True, methods=["post"])
    def poke(self, request, pk=None):
        return Response({})


class QuietRetrieveViewSet(PreconditionMixin, ModelViewSet):
    """A retrieve routed, and left out of precondition_read_actions - it sends no ETag."""

    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    precondition_read_actions = ()


class VersionedWidgetReadView(PreconditionMixin, RetrieveAPIView):
    queryset = VersionedWidget.objects.all()
    serializer_class = serializers.Serializer


class WidgetWriteView(PreconditionMixin, UpdateAPIView):
    """Routed at the same path as a read of another model."""

    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer


class UncheckedWidgetView(PreconditionMixin, GenericAPIView):
    """Not a viewset; its GET names one object and never fetches it through get_object()."""

    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer

    def get(self, request, pk):
        return Response({})


class UncheckedNoteViewSet(PreconditionMixin, GenericViewSet):
    """A read over a model with no concurrency token - nothing to send, so nothing to refuse."""

    queryset = CleanedNote.objects.all()
    serializer_class = serializers.Serializer
    precondition_read_actions = ("me",)

    @action(detail=False, methods=["get"])
    def me(self, request):
        return Response({})


class ModellessReadViewSet(PreconditionMixin, GenericViewSet):
    serializer_class = serializers.Serializer
    precondition_read_actions = ("me",)

    @action(detail=False, methods=["get"])
    def me(self, request):
        return Response({})


class MixedReadViewSet(PreconditionMixin, RetrieveModelMixin, GenericViewSet):
    """Routed at the same prefix as MixedWriteViewSet, reading another model."""

    queryset = VersionedWidget.objects.all()
    serializer_class = serializers.Serializer


class MixedWriteViewSet(PreconditionMixin, UpdateModelMixin, GenericViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
