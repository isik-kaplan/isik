"""project_urlconfs() - every urlconf a project serves: ROOT_URLCONF, and each django-hosts host's if installed."""

import sys

import pytest

from isik.django.apps.common.urlconfs import project_urlconfs
from tests.django.drf import test_coverage


HOSTS = "tests.django.apps.common.hosts"
HOSTED = "tests.django.apps.common.hosted_urls"
COVERAGE = "tests.django.drf.test_coverage"


class TestTheProjectsOwn:
    def test_none_without_a_root_urlconf(self, settings):
        del settings.ROOT_URLCONF

        assert project_urlconfs() == []

    def test_root_urlconf_alone_without_django_hosts_configured(self, settings):
        settings.ROOT_URLCONF = "config.urls"

        assert project_urlconfs() == ["config.urls"]

    @pytest.mark.parametrize("hostconf", ["", None])
    def test_an_empty_hostconf_adds_nothing(self, settings, hostconf):
        settings.ROOT_URLCONF = "config.urls"
        settings.ROOT_HOSTCONF = hostconf

        assert project_urlconfs() == ["config.urls"]

    def test_every_hosts_urlconf_follows_root_urlconf(self, settings):
        settings.ROOT_URLCONF = "config.urls"
        settings.ROOT_HOSTCONF = HOSTS

        assert project_urlconfs() == ["config.urls", HOSTED, COVERAGE]

    def test_a_host_serving_root_urlconf_is_walked_once(self, settings):
        settings.ROOT_URLCONF = COVERAGE
        settings.ROOT_HOSTCONF = HOSTS

        assert project_urlconfs() == [COVERAGE, HOSTED]

    def test_without_django_hosts_installed_its_setting_is_ignored(self, settings, monkeypatch):
        settings.ROOT_URLCONF = "config.urls"
        settings.ROOT_HOSTCONF = HOSTS
        monkeypatch.setitem(sys.modules, "django_hosts.resolvers", None)  # import fails, as if not installed

        assert project_urlconfs() == ["config.urls"]


class TestNamedOnes:
    def test_one_by_name_or_as_a_module(self, settings):
        settings.ROOT_HOSTCONF = HOSTS

        assert project_urlconfs(HOSTED) == [HOSTED]
        assert project_urlconfs(test_coverage) == [test_coverage]

    @pytest.mark.parametrize("several", [[HOSTED, COVERAGE, HOSTED], (HOSTED, COVERAGE, HOSTED)])
    def test_several_in_a_list_or_tuple_each_once(self, several):
        assert project_urlconfs(several) == [HOSTED, COVERAGE]
