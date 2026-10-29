"""Who a notice about an organization reaches - its members."""

from django.contrib.auth import get_user_model


def of(organization):
    return get_user_model().objects.filter(pk__in=organization.memberships.values("user"))
