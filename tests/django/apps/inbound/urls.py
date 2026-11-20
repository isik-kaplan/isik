from django.urls import include, path

from isik.django.apps.inbound.views import inbound_urls


urlpatterns = [path("in/", include(inbound_urls()))]
