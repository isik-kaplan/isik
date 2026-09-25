from isik._internal import check_extra


check_extra("drf", "rest_framework")

from isik.django.drf.error_handling import django_to_drf_validation_error  # noqa: E402
from isik.django.drf.filters import make_filters  # noqa: E402
from isik.django.drf.pagination import PageNumberPagination  # noqa: E402
from isik.django.drf.permissions import (  # noqa: E402
    ANY_VALUE,
    AnyValueTarget,
    EqualsTarget,
    Guard,
    GuardTarget,
    IsAnonymous,
    IsAuthenticatedANDSignupCompleted,
    IsSuperUser,
    MatchingTarget,
    NotOneOfTarget,
    OneOfTarget,
    ReadOnly,
    descriptor_attribute_name,
    evaluate_permission,
    guarding,
    guarding_matching,
    guarding_other_than,
    guarding_values,
    is_owner,
    object_property,
    only_actions,
    permission_name,
    prevent_actions,
    user_property,
)
from isik.django.drf.schema import FakeErrorSerializer, FakeSerializer  # noqa: E402


__all__ = [
    "ANY_VALUE",
    "AnyValueTarget",
    "EqualsTarget",
    "FakeErrorSerializer",
    "FakeSerializer",
    "Guard",
    "GuardTarget",
    "IsAnonymous",
    "IsAuthenticatedANDSignupCompleted",
    "IsSuperUser",
    "MatchingTarget",
    "NotOneOfTarget",
    "OneOfTarget",
    "PageNumberPagination",
    "ReadOnly",
    "descriptor_attribute_name",
    "django_to_drf_validation_error",
    "evaluate_permission",
    "guarding",
    "guarding_matching",
    "guarding_other_than",
    "guarding_values",
    "is_owner",
    "make_filters",
    "object_property",
    "only_actions",
    "permission_name",
    "prevent_actions",
    "user_property",
]
