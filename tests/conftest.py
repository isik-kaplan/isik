import pytest
from django.contrib.admin.sites import AdminSite
from hypothesis import HealthCheck, settings


# mutmut re-invokes pytest.main() in-process (not a fresh subprocess) for its baseline/clean-test
# run and for each mutant - Hypothesis's differing_executors check flags exactly that pattern as a
# potential correctness issue, since it's usually a sign of real nondeterminism. Here it's an
# artifact of the mutation-testing harness itself, not the tests - selected only via
# --hypothesis-profile=mutation (see pytest_add_cli_args in [tool.mutmut]), never the default.
settings.register_profile("mutation", suppress_health_check=[HealthCheck.differing_executors])


@pytest.fixture
def admin_site():
    return AdminSite()


@pytest.fixture
def admin_user(django_user_model):
    return django_user_model.objects.create_superuser(username="admin", email="admin@example.com", password="password")
