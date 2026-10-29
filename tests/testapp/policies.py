"""A request policy the test project gates a setting with - a fresh proof of the change it's for, read off a header
for the tests."""

from isik.django.drf.viewsets.request_policies import RequestPolicy


class RecentlyProved(RequestPolicy):
    message = "Prove who you are first."
    code = "reauthentication_required"

    def allows(self, request, view):
        return request.headers.get("X-Proof") == f"fresh for {view.kwargs['name']}"

    def refused(self, request, view, response):
        response["X-Reauthentication-Required"] = "true"
