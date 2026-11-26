from isik._internal import check_extra


check_extra("django-admin", "django_object_actions")
check_extra("django-admin", "dalf")

from isik.django.apps.common.admin.base import BaseAdmin  # noqa: E402
from isik.django.apps.common.admin.decorators import action  # noqa: E402


__all__ = [
    "BaseAdmin",
    "action",
]
