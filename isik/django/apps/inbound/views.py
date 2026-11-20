"""
The view a sender posts to - one URL per source - and `inbound_urls()`, which routes them:

    urlpatterns = [path("webhooks/", include(inbound_urls()))]     # POST /webhooks/<source>/

A request is answered at once - 200 once the event is written down, or a resend of one written already; 404 for a
source not declared, 401 for a request its verifier refuses, 400 for a body its source can't read. The event is
handled by a task, after the answer.
"""

import logging

from django.http import HttpResponse, JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from isik._internal.translation import gettext as _
from isik.django.apps.inbound.pipeline import in_partition, record
from isik.django.apps.inbound.sources import Unverified, sources


logger = logging.getLogger(__name__)


def _refused(status, detail, code):
    return JsonResponse({"detail": detail, "code": code}, status=status)


@csrf_exempt
@require_POST
def receive(request, source):
    """Verifies, reads and writes down one webhook of `source`, in the partition it names - then answers."""
    declared = sources.get(source)
    if declared is None:
        return _refused(404, _("No source called %(source)s is declared.") % {"source": source}, "unknown_source")
    partition = declared.partition(request) if declared.partition is not None else None
    with in_partition(partition):
        try:
            declared.verifier.verify(request)
        except Unverified as refused:
            logger.warning("An inbound %s webhook was refused: %s", source, refused)
            return _refused(401, str(refused), "unverified")
        try:
            payload = declared.parse(request)
            event_id = declared.event_id(request, payload) if declared.identifies_events else None
            event_type = declared.event_type(request, payload)
        except (ValueError, KeyError, TypeError, IndexError) as unreadable:
            return _refused(
                400,
                _("The body isn't one %(source)s sends: %(error)s") % {"source": source, "error": unreadable},
                "unreadable",
            )
        if declared.identifies_events and not event_id:
            return _refused(400, _("The body names no event id."), "unidentified")
        record(declared, None if event_id is None else str(event_id), str(event_type), payload, partition)
    return HttpResponse()


def inbound_urls():
    """The URL patterns receiving every declared source's webhooks, at `<source>/`."""
    return [path("<str:source>/", receive, name="isik-inbound-receive")]
