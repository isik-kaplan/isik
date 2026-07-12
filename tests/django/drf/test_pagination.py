import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from isik.django.drf.pagination import PageNumberPagination


@pytest.fixture
def make_request():
    factory = APIRequestFactory()
    return lambda *args, **kwargs: Request(factory.get(*args, **kwargs))


@pytest.fixture
def paginator():
    instance = PageNumberPagination()
    instance.page_size = 2
    return instance


def test_page_size_query_param_is_page_size():
    assert PageNumberPagination.page_size_query_param == "page_size"


def test_paginate_queryset_respects_the_page_size_query_param(make_request, paginator):
    request = make_request("/", {"page_size": 1})
    page = paginator.paginate_queryset(list(range(5)), request)
    assert page == [0]


def test_get_paginated_response_includes_count_page_size_and_total_pages(make_request, paginator):
    request = make_request("/", {"page": 1})
    page = paginator.paginate_queryset(list(range(5)), request)
    response = paginator.get_paginated_response(page)
    assert response.data["count"] == 5
    assert response.data["page_size"] == 2
    assert response.data["total_pages"] == 3
    assert response.data["results"] == [0, 1]


def test_get_paginated_response_schema_lists_the_expected_fields():
    schema = PageNumberPagination().get_paginated_response_schema({"type": "object", "items": {}})
    assert schema["required"] == ["count", "page_size", "total_pages", "results"]
    assert set(schema["properties"]) == {"count", "page_size", "total_pages", "results"}
    assert schema["properties"]["results"] == {"type": "object", "items": {}}
