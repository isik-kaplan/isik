from django.urls import include, path
from rest_framework.routers import SimpleRouter

from isik.django.apps.sync.drf import SyncViewSet
from tests.testapp import sync_views as views


api = SimpleRouter()
api.register("tasks", views.TaskViewSet, basename="task")
api.register("projects", views.ProjectViewSet, basename="project")
api.register("notes", views.NoteViewSet, basename="note")
api.register("boards", views.BoardViewSet, basename="board")

sync = SimpleRouter(trailing_slash=False)
sync.register("sync", SyncViewSet, basename="sync")

urlpatterns = [path("api/", include(api.urls)), *sync.urls]
