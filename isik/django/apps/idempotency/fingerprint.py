"""request_fingerprint() + canonical_body()/canonical_json() - what makes two requests the same one."""

import hashlib
import json

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.http import QueryDict
from django.utils.module_loading import import_string

from isik._internal.translation import gettext as _


def _describe_upload(value):
    if not isinstance(value, UploadedFile):
        raise TypeError(
            _(
                "Can't fingerprint a value of type %(type)s. Pass a normalizer that turns the body into JSON - "
                "idempotency_normalize on the view, or IDEMPOTENCY_NORMALIZE."
            )
            % {"type": type(value).__name__}
        )
    digest = hashlib.sha256()
    for chunk in value.chunks():
        digest.update(chunk)
    # chunks() leaves the file read to its end - the handler reading it next starts from the top.
    value.seek(0)
    return {"name": value.name, "size": value.size, "sha256": digest.hexdigest()}


def canonical_json(value):
    """
    `value` as JSON that comes out the same whatever order its keys were in - and an uploaded file
    as its name, size and the sha256 of its content, so a retried upload matches whatever boundary
    its multipart body was sent with.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_describe_upload)


def canonical_body(request):
    """
    The default normalizer: the request's parsed body, which `canonical_json()` then writes out with
    sorted keys. A form or multipart body keeps every value each field was sent, in order.
    """
    data = request.data
    if isinstance(data, QueryDict):
        return {key: data.getlist(key) for key in data}
    return data


def configured_normalize():
    """`IDEMPOTENCY_NORMALIZE` (a callable, or a dotted path to one), else `canonical_body`."""
    normalize = getattr(settings, "IDEMPOTENCY_NORMALIZE", None) or canonical_body
    return import_string(normalize) if isinstance(normalize, str) else normalize


def request_fingerprint(request, normalize=None):
    """
    sha256 over the method, path, query and `normalize(request)` - `configured_normalize()` unless
    given. A normalizer returns anything `canonical_json()` can write, so one that only drops a
    field can build on the default:

        def without_nonce(request):
            return {key: value for key, value in canonical_body(request).items() if key != "nonce"}
    """
    normalize = normalize or configured_normalize()
    described = {
        "method": request.method,
        "path": request.path,
        "query": sorted(request.query_params.lists()),
        "body": normalize(request),
    }
    return hashlib.sha256(canonical_json(described).encode()).hexdigest()
