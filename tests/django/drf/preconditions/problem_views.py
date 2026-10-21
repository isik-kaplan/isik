"""Views whose tokens can't be trusted, one way each - routed by `problem_urls.py` for the check's tests."""

from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from isik.django.drf.viewsets import ActionSerializerClassMixin, PreconditionMixin
from tests.testapp.models import CleanedNote, VersionedWidget, Widget


class CleanedNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CleanedNote
        fields = ["id", "count"]


class StampWritingSerializer(serializers.ModelSerializer):
    # Declared outright, so not the read-only field ModelSerializer would have made of it.
    updated_at = serializers.DateTimeField()

    class Meta:
        model = Widget
        fields = ["id", "name", "updated_at"]


class StampRenamingSerializer(serializers.ModelSerializer):
    stamp = serializers.DateTimeField(source="updated_at")

    class Meta:
        model = Widget
        fields = ["id", "stamp"]


class VersionWritingSerializer(serializers.ModelSerializer):
    version = serializers.IntegerField()

    class Meta:
        model = VersionedWidget
        fields = ["id", "version"]


class UntokenedViewSet(PreconditionMixin, ModelViewSet):
    queryset = CleanedNote.objects.all()
    serializer_class = CleanedNoteSerializer


class AlsoUntokenedViewSet(PreconditionMixin, ModelViewSet):
    """The same model's problem again - reported once."""

    queryset = CleanedNote.objects.all()
    serializer_class = CleanedNoteSerializer


class ModellessViewSet(PreconditionMixin, GenericViewSet):
    @action(detail=True, methods=["post"])
    def poke(self, request, pk=None):
        return Response({})


class StampWritingViewSet(PreconditionMixin, ActionSerializerClassMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = StampWritingSerializer
    serializer_class_action_map = {"partial_update": StampRenamingSerializer, "update": StampWritingSerializer}


class VersionWritingViewSet(PreconditionMixin, ModelViewSet):
    queryset = VersionedWidget.objects.all()
    serializer_class = VersionWritingSerializer


class SerializerlessViewSet(PreconditionMixin, ModelViewSet):
    """Picks its serializer in get_serializer_class() - nothing declared to check."""

    queryset = Widget.objects.all()

    def get_serializer_class(self):
        return StampWritingSerializer


class PlainViewSet(ModelViewSet):
    """No PreconditionMixin - nothing to check."""

    queryset = CleanedNote.objects.all()
    serializer_class = CleanedNoteSerializer


class NoDeletesViewSet(PreconditionMixin, ModelViewSet):
    """Its token is fine; its deletes don't ask for it - uncovered, for the coverage report."""

    queryset = VersionedWidget.objects.all()
    serializer_class = serializers.Serializer
    precondition_methods = ("PUT", "PATCH", "POST")
