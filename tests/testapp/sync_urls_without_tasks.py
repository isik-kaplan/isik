"""The synced models' REST APIs, tasks', boards' and notes' left out - a model routed nowhere."""

from django.urls import include, path
from rest_framework.routers import SimpleRouter

from tests.testapp import sync_views as views


api = SimpleRouter()
api.register("projects", views.ProjectViewSet, basename="project")

urlpatterns = [path("api/", include(api.urls))]
