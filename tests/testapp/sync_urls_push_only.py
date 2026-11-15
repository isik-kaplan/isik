"""The sync endpoints alone - push writing a model whose REST API is routed nowhere."""

from rest_framework.routers import SimpleRouter

from isik.django.apps.sync.drf import SyncViewSet


sync = SimpleRouter(trailing_slash=False)
sync.register("sync", SyncViewSet, basename="sync")

urlpatterns = sync.urls
