from isik._internal import check_extra


check_extra("drf", "rest_framework")

from isik.django.drf.error_handling import django_to_drf_validation_error  # noqa: E402
from isik.django.drf.filters import make_filters  # noqa: E402
from isik.django.drf.pagination import PageNumberPagination  # noqa: E402
from isik.django.drf.permissions import (  # noqa: E402
    IsAnonymous,
    IsAuthenticatedANDSignupCompleted,
    IsSuperUser,
    ReadOnly,
    is_owner,
    prevent_actions,
    user_property,
)
from isik.django.drf.schema import FakeErrorSerializer, FakeSerializer  # noqa: E402


__all__ = [
    "FakeErrorSerializer",
    "FakeSerializer",
    "IsAnonymous",
    "IsAuthenticatedANDSignupCompleted",
    "IsSuperUser",
    "PageNumberPagination",
    "ReadOnly",
    "django_to_drf_validation_error",
    "is_owner",
    "make_filters",
    "prevent_actions",
    "user_property",
]
