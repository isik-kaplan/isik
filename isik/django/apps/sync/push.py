"""
Push: a batch of a client's queued writes in one request, each run as the REST write it stands for - a backend's
business. `PushBackend` is what a backend is; `ViewPushBackend`, isik's own, runs each entry through its
model's REST view, in a transaction of its own.

    ISIK['DJANGO']['SYNC']['PUSH'] = {"BACKEND": "acme.sync.QueuedPushBackend", "OPTIONS": {"MAX_ENTRIES": 200}}

Whatever the backend, an entry's result is `{key, status, etag?, data? | current? | errors?}`, in entry order.
"""

import contextlib
import io
import logging
import uuid

import pghistory
from django.core.handlers.wsgi import WSGIRequest
from django.db import transaction
from rest_framework import status
from rest_framework.renderers import JSONRenderer
from rest_framework.schemas.generators import EndpointEnumerator

from isik.django.apps.common.db.concurrency import current_concurrency_etag
from isik.django.apps.sync.declarations import synced_models
from isik.django.apps.sync.reading import viewset_of


logger = logging.getLogger(__name__)

# Each method an entry may name: (the viewset's method, its action).
ACTIONS = {
    "POST": ("post", "create"),
    "PUT": ("put", "update"),
    "PATCH": ("patch", "partial_update"),
    "DELETE": ("delete", "destroy"),
}
# An entry's key that isn't a UUID is made one in this namespace - the same key, the same UUID - since an
# Idempotency-Key is a UUID to REST.
KEY_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "isik.django.apps.sync.push")
ETAG = "ETag"
JSON = "application/json"
# What a request carries that an entry's request takes from the entry instead.
_ENTRY_HEADERS = ("CONTENT_TYPE", "CONTENT_LENGTH", "HTTP_IDEMPOTENCY_KEY", "HTTP_IF_MATCH", "HTTP_IF_NONE_MATCH")
# A request's own parts, which an entry's request has its own of - every other attribute it carries,
# middleware's among them, the entry's request carries too.
_OWN_ATTRIBUTES = {
    "environ",
    "path_info",
    "path",
    "META",
    "method",
    "content_type",
    "content_params",
    "_stream",
    "_read_started",
    "resolver_match",
    "GET",
    "POST",
    "COOKIES",
    "FILES",
    "_body",
    "_post",
    "_files",
    "_encoding",
    "_upload_handlers",
    "headers",
}


def entry_key(key):
    """The Idempotency-Key an entry's `key` is: itself when it's a UUID, else the UUID it names."""
    try:
        return str(uuid.UUID(key))
    except ValueError:
        return str(uuid.uuid5(KEY_NAMESPACE, key))


@contextlib.contextmanager
def entry_context(index):
    """
    A history context of the entry's own - the request's, plus `sync_entry`, the entry's place in the push - so
    the rows each entry writes are told apart from the others'.
    """
    from pghistory import runtime

    outer = getattr(runtime._tracker, "value", None)
    if outer is None:
        with pghistory.context(sync_entry=index):
            yield
        return
    runtime._tracker.value = runtime.Context(id=uuid.uuid4(), metadata={**outer.metadata, "sync_entry": index})
    try:
        yield
    finally:
        runtime._tracker.value = outer


class PushBackend:
    """
    What runs a push's entries - `run(request, entries)`, a result per entry, in their order. A backend is built
    with `PUSH["OPTIONS"]`; the options every backend takes are `MAX_ENTRIES`, `MAX_BYTES` and `THROTTLE_SCOPE`.

    A backend commits nothing for an entry whose result isn't a 2xx - not its write, not its idempotency key.
    """

    # The name the capabilities document advertises - the class's dotted path unless set.
    name = None

    def __init__(self, options):
        self.options = options

    @property
    def advertised_name(self):
        return self.name or f"{type(self).__module__}.{type(self).__qualname__}"

    @property
    def max_entries(self):
        """The most entries one push holds - None for no limit."""
        return self.options.get("MAX_ENTRIES")

    @property
    def max_bytes(self):
        """The most bytes one push's body holds - None for no limit."""
        return self.options.get("MAX_BYTES")

    @property
    def throttle_scope(self):
        """The DRF throttle scope counting a person's pushed entries - None for none."""
        return self.options.get("THROTTLE_SCOPE")

    def run(self, request, entries):
        raise NotImplementedError


class ViewPushBackend(PushBackend):
    """
    Runs each entry through its model's REST view - its serializer, its permissions, its idempotency and
    preconditions - in order, each in a transaction of its own, so a long push holds no transaction open and the
    feed's watermark moves on as it goes. An entry failing - refused, or raising - is that entry's result alone.

    An entry's request is the push's own - its user, its session, whatever its middleware set on it - with the
    entry's method, path, body, `Idempotency-Key` and `If-Match`. The view's throttles don't run for it, but those
    a `PerEntryThrottle` marks, against the person's real rate.
    """

    def run(self, request, entries):
        # The urlconf the push was routed by - a host's own, under django-hosts - which routes its entries too.
        endpoints = EndpointEnumerator(urlconf=getattr(request._request, "urlconf", None)).get_api_endpoints()
        routes = [(path, callback) for path, _, callback in endpoints]
        return [self.run_entry(request, index, entry, routes) for index, entry in enumerate(entries)]

    def run_entry(self, request, index, entry, routes):
        synced = synced_models().get(entry["model"])
        if synced is None:
            return {"key": entry["key"], "status": status.HTTP_404_NOT_FOUND, "errors": {"code": "sync_model_unknown"}}
        try:
            with transaction.atomic(), entry_context(index):
                response = self.dispatch(request, synced, entry, routes)
                if not status.is_success(response.status_code):
                    transaction.set_rollback(True)
        except Exception:
            logger.exception("Push entry %s (%s %s) raised.", entry["key"], entry["method"], entry["model"])
            return {"key": entry["key"], "status": status.HTTP_500_INTERNAL_SERVER_ERROR, "errors": {"code": "error"}}
        return result(entry, response)

    def dispatch(self, request, synced, entry, routes):
        from isik.django.apps.sync.drf import PerEntryThrottle

        viewset = viewset_of(synced)
        handler, action = ACTIONS[entry["method"]]
        throttles = [each for each in viewset.throttle_classes if issubclass(each, PerEntryThrottle)]
        view = viewset.as_view({handler: action}, throttle_classes=throttles)
        if action == "create":
            body = {synced.model._meta.pk.name: entry["id"], **entry.get("body", {})}
            path = route(viewset, routes, action) or f"/{synced.name}/"
            return view(sub_request(request, entry, path, body))
        lookup = viewset.lookup_url_kwarg or viewset.lookup_field
        path = (route(viewset, routes, action) or f"/{synced.name}/{{{lookup}}}/").replace(f"{{{lookup}}}", entry["id"])
        return view(sub_request(request, entry, path, entry.get("body")), **{lookup: entry["id"]})


def route(viewset, routes, action):
    """
    The path `viewset` serves `action` at, of `routes` - (path, callback) pairs, a URI template naming its lookup as
    `{pk}` - or None. A viewset routed nowhere is still the one serving its model: an entry's request then names a
    path of the model's own, which only an idempotency key's fingerprint reads.
    """
    return next(
        (path for path, callback in routes if callback.cls is viewset and action in callback.actions.values()), None
    )


def sub_request(request, entry, path, body):
    """The request an entry stands for - see `ViewPushBackend`."""
    outer = request._request
    content = JSONRenderer().render(body)
    environ = {key: value for key, value in outer.META.items() if key not in _ENTRY_HEADERS}
    # The push's own SCRIPT_NAME kept, so the entry's path is the one its REST request would have.
    environ.update(
        {
            "REQUEST_METHOD": entry["method"],
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_TYPE": JSON,
            "CONTENT_LENGTH": str(len(content)),
            "wsgi.input": io.BytesIO(content),
            "HTTP_IDEMPOTENCY_KEY": entry_key(entry["key"]),
        }
    )
    if "if_match" in entry:
        environ["HTTP_IF_MATCH"] = entry["if_match"]
    made = WSGIRequest(environ)
    made.__dict__.update({name: value for name, value in outer.__dict__.items() if name not in _OWN_ATTRIBUTES})
    # Authenticated as the push was, by whatever authenticated it.
    made._force_auth_user = request.user
    made._force_auth_token = request.auth
    return made


def result(entry, response):
    """An entry's result, from the response its view answered with."""
    answered = {"key": entry["key"], "status": response.status_code}
    data = response.data
    if status.is_success(response.status_code):
        etag = response.get(ETAG) or _etag_of(data)
        if etag is not None:
            answered["etag"] = etag
        if data is not None:
            answered["data"] = data
    elif response.status_code == status.HTTP_412_PRECONDITION_FAILED:
        if response.get(ETAG):
            answered["etag"] = response[ETAG]
        if data.get("current") is not None:
            answered["current"] = data["current"]
    else:
        answered["errors"] = data
    return answered


def _etag_of(data):
    # A replay answered by reference re-serializes the row as it is now, without the ETag a write sends: the
    # row's own is read again.
    instance = getattr(getattr(data, "serializer", None), "instance", None)
    if instance is None:
        return None
    return current_concurrency_etag(type(instance), instance.pk)


__all__ = ["PushBackend", "ViewPushBackend", "entry_context", "entry_key", "route"]
