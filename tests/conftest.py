import pytest
from django.contrib.admin.sites import AdminSite


@pytest.fixture
def admin_site():
    return AdminSite()


@pytest.fixture
def admin_user(django_user_model):
    return django_user_model.objects.create_superuser(username="admin", email="admin@example.com", password="password")
