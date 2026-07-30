from django.conf import settings
from rest_framework import pagination
from rest_framework.response import Response


class PageNumberPagination(pagination.PageNumberPagination):
    page_size_query_param = "page_size"
    max_page_size = None

    def get_page_size(self, request):
        """Cap page_size at settings.DRF_PAGINATION_MAX_PAGE_SIZE (default 1000).

        Set `max_page_size` on a subclass to override the settings lookup entirely.
        """
        self.max_page_size = self.max_page_size or getattr(settings, "DRF_PAGINATION_MAX_PAGE_SIZE", 1000)
        return super().get_page_size(request)

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "page_size": self.get_page_size(self.request),
                "total_pages": self.page.paginator.num_pages,
                "results": data,
            }
        )

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Total number of items available.",
                    "example": 1102,
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of results to return per page.",
                    "example": 100,
                },
                "total_pages": {
                    "type": "integer",
                    "description": "Total number of pages.",
                    "example": 17,
                },
                "results": schema,
            },
            "required": ["count", "page_size", "total_pages", "results"],
        }
