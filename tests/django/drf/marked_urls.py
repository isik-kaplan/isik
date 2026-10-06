"""Views decorated in the urlconf itself, where only the routed callable carries the mark."""

from django.urls import path

from tests.django.drf.test_coverage import DjangoView, PlainView, not_drf


def marked(callback):
    callback.marked_in_the_urlconf = True
    return callback


urlpatterns = [
    path("drf/", marked(PlainView.as_view())),
    path("class/", marked(DjangoView.as_view())),
    path("function/", marked(not_drf)),
]
