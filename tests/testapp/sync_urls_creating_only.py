"""Tasks' REST API taking creates alone - a synced model with no retrieve routed."""

from django.urls import path

from tests.testapp import sync_views as views


urlpatterns = [path("api/tasks/", views.TaskViewSet.as_view({"post": "create"}))]
