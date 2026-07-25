# pagination

`PageNumberPagination` is DRF's page-number pagination with a `?page_size=` query param and a response shape that includes `total_pages` alongside the usual count/results.

```python
from isik.django.drf.pagination import PageNumberPagination

class WidgetViewSet(ModelViewSet):
    pagination_class = PageNumberPagination

# GET /widgets/?page=1&page_size=2
# -> {"count": 5, "page_size": 2, "total_pages": 3, "results": [...]}
```

- `get_paginated_response_schema` is overridden to match, for drf-spectacular/OpenAPI generation to describe the actual response shape instead of DRF's default `next`/`previous`/`results`.
