from rest_framework import serializers

from isik.django.apps.sync.drf import ClientPrimaryKeyMixin
from isik.django.drf.viewsets import BaseModelViewSet
from isik.django.drf.viewsets.schema_generation import none_during_schema_generation
from tests.testapp.models import SyncBoard, SyncNote, SyncProject, SyncTask


class ProjectSerializer(ClientPrimaryKeyMixin, serializers.ModelSerializer):
    class Meta:
        model = SyncProject
        fields = ["id", "owner", "name"]


class TaskSerializer(ClientPrimaryKeyMixin, serializers.ModelSerializer):
    class Meta:
        model = SyncTask
        fields = ["id", "owner", "title", "done", "project"]


class NoteSerializer(ClientPrimaryKeyMixin, serializers.ModelSerializer):
    class Meta:
        model = SyncNote
        fields = ["id", "team", "body"]


CLAIMS = "idempotency_by_reference.IdempotencyClaim"


class ProjectViewSet(BaseModelViewSet):
    idempotency_claim_model = CLAIMS
    model = SyncProject
    endpoint = "projects"
    serializer_class = ProjectSerializer


class TaskViewSet(BaseModelViewSet):
    idempotency_claim_model = CLAIMS
    model = SyncTask
    endpoint = "tasks"
    serializer_class = TaskSerializer

    @none_during_schema_generation
    def get_queryset(self):
        # A person's own tasks - what the feed reads them through, too.
        return super().get_queryset().filter(owner=self.request.user)


class NoteViewSet(BaseModelViewSet):
    idempotency_claim_model = CLAIMS
    model = SyncNote
    endpoint = "notes"
    serializer_class = NoteSerializer


class BoardSerializer(ClientPrimaryKeyMixin, serializers.ModelSerializer):
    class Meta:
        model = SyncBoard
        fields = ["id", "owner", "teams", "color"]


class BoardViewSet(BaseModelViewSet):
    idempotency_claim_model = CLAIMS
    model = SyncBoard
    endpoint = "boards"
    serializer_class = BoardSerializer
