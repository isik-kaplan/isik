from django.db import transaction
from django.http import HttpResponse
from django.urls import path
from django.utils.decorators import method_decorator
from django.views import View


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class SilentExport(View):
    def get(self, request):
        return HttpResponse("ok")

    def post(self, request):
        return HttpResponse("ok")


urlpatterns = [path("export/", SilentExport.as_view())]
