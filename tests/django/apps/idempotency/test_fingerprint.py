"""What makes two requests the same one, as far as an idempotency key is concerned."""

import hashlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.test.client import encode_multipart
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from isik.django.apps.idempotency.fingerprint import (
    canonical_body,
    canonical_json,
    configured_normalize,
    request_fingerprint,
)


PARSERS = [JSONParser(), FormParser(), MultiPartParser()]


def drf_request(django_request):
    return Request(django_request, parsers=PARSERS)


def json_request(data, path="/widgets/", method="post"):
    return drf_request(getattr(APIRequestFactory(), method)(path, data, format="json"))


def multipart_request(data, boundary):
    body = encode_multipart(boundary, data)
    return drf_request(
        APIRequestFactory().generic("POST", "/widgets/", body, content_type=f"multipart/form-data; boundary={boundary}")
    )


def body_only(request):
    return {"only": "body"}


def test_canonical_json_sorts_keys_at_every_depth_and_wastes_no_space():
    assert canonical_json({"b": 1, "a": {"d": [2, 1], "c": None}}) == '{"a":{"c":null,"d":[2,1]},"b":1}'


def test_canonical_json_describes_an_upload_by_its_content():
    upload = SimpleUploadedFile("bolt.txt", b"threads")

    described = canonical_json({"file": upload})

    sha = hashlib.sha256(b"threads").hexdigest()
    assert described == f'{{"file":{{"name":"bolt.txt","sha256":"{sha}","size":7}}}}'
    # The handler reading it next gets all of it, not what was left after hashing.
    assert upload.read() == b"threads"


def test_canonical_json_hashes_an_upload_bigger_than_one_chunk_whole():
    content = b"x" * (SimpleUploadedFile.DEFAULT_CHUNK_SIZE + 5)

    described = canonical_json(SimpleUploadedFile("big.bin", content))

    assert hashlib.sha256(content).hexdigest() in described


def test_canonical_json_refuses_what_it_cannot_write_and_says_where_to_turn():
    with pytest.raises(TypeError) as raised:
        canonical_json({"when": object()})

    assert str(raised.value) == (
        "Can't fingerprint a value of type object. Pass a normalizer that turns the body into JSON - "
        "idempotency_normalize on the view, or IDEMPOTENCY_NORMALIZE."
    )


def test_canonical_body_is_the_parsed_json():
    assert canonical_body(json_request({"name": "bolt", "tags": ["a", "b"]})) == {"name": "bolt", "tags": ["a", "b"]}


def test_canonical_body_keeps_every_value_a_form_field_was_sent():
    request = drf_request(
        APIRequestFactory().post("/widgets/", "tag=b&tag=a&name=bolt", content_type="application/x-www-form-urlencoded")
    )

    assert canonical_body(request) == {"tag": ["b", "a"], "name": ["bolt"]}


def test_the_default_normalizer_is_canonical_body():
    assert configured_normalize() is canonical_body


@override_settings(IDEMPOTENCY_NORMALIZE=body_only)
def test_the_normalizer_setting_takes_a_callable():
    assert configured_normalize() is body_only


@override_settings(IDEMPOTENCY_NORMALIZE="tests.django.apps.idempotency.test_fingerprint.body_only")
def test_the_normalizer_setting_takes_a_dotted_path():
    assert configured_normalize() is body_only


def test_the_fingerprint_is_sha256_over_method_path_query_and_body():
    request = json_request({"name": "bolt"}, path="/widgets/?b=2&a=1&a=0")

    described = '{"body":{"name":"bolt"},"method":"POST","path":"/widgets/","query":[["a",["1","0"]],["b",["2"]]]}'
    assert request_fingerprint(request) == hashlib.sha256(described.encode()).hexdigest()


def test_reordered_json_is_the_same_request():
    first = json_request({"name": "bolt", "count": 1})
    retry = drf_request(
        APIRequestFactory().post("/widgets/", '{"count": 1, "name": "bolt"}', content_type="application/json")
    )

    assert request_fingerprint(first) == request_fingerprint(retry)


def test_reordered_query_parameters_are_the_same_request():
    assert request_fingerprint(json_request({}, path="/widgets/?a=1&b=2")) == request_fingerprint(
        json_request({}, path="/widgets/?b=2&a=1")
    )


@pytest.mark.parametrize(
    "retry",
    [
        lambda: json_request({"name": "nut"}),
        lambda: json_request({"name": "bolt"}, path="/gadgets/"),
        lambda: json_request({"name": "bolt"}, path="/widgets/?dry_run=1"),
        lambda: json_request({"name": "bolt"}, method="put"),
    ],
    ids=["body", "path", "query", "method"],
)
def test_a_change_to_any_part_is_a_different_request(retry):
    assert request_fingerprint(json_request({"name": "bolt"})) != request_fingerprint(retry())


def test_a_retried_upload_is_the_same_request_whatever_its_boundary():
    def upload(boundary):
        return multipart_request({"name": "bolt", "file": SimpleUploadedFile("a.txt", b"one")}, boundary)

    assert request_fingerprint(upload("first-boundary")) == request_fingerprint(upload("second-boundary"))


def test_a_different_upload_is_a_different_request():
    first = multipart_request({"file": SimpleUploadedFile("a.txt", b"one")}, "boundary")
    retry = multipart_request({"file": SimpleUploadedFile("a.txt", b"two")}, "boundary")

    assert request_fingerprint(first) != request_fingerprint(retry)


@override_settings(IDEMPOTENCY_NORMALIZE=body_only)
def test_the_configured_normalizer_decides_what_the_body_is():
    assert request_fingerprint(json_request({"name": "bolt"})) == request_fingerprint(json_request({"name": "nut"}))


@override_settings(IDEMPOTENCY_NORMALIZE=body_only)
def test_a_normalizer_passed_in_wins_over_the_configured_one():
    def name_only(request):
        return request.data["name"]

    assert request_fingerprint(json_request({"name": "bolt"}), name_only) != request_fingerprint(
        json_request({"name": "nut"}), name_only
    )
