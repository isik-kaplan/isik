from isik._internal import check_extra


check_extra("drf", "rest_framework")

from isik.django.drf.error_handling import django_to_drf_validation_error  # noqa: E402
from isik.django.drf.filters import make_filters  # noqa: E402
from isik.django.drf.pagination import PageNumberPagination  # noqa: E402
from isik.django.drf.permissions import (  # noqa: E402
    Guard,
    IsAnonymous,
    IsAuthenticatedANDSignupCompleted,
    IsSuperUser,
    ReadOnly,
    guarding,
    is_owner,
    object_property,
    prevent_actions,
    user_property,
)
from isik.django.drf.schema import FakeErrorSerializer, FakeSerializer  # noqa: E402


__all__ = [
    "FakeErrorSerializer",
    "FakeSerializer",
    "Guard",
    "IsAnonymous",
    "IsAuthenticatedANDSignupCompleted",
    "IsSuperUser",
    "PageNumberPagination",
    "ReadOnly",
    "django_to_drf_validation_error",
    "guarding",
    "is_owner",
    "make_filters",
    "object_property",
    "prevent_actions",
    "user_property",
]
