"""A django-hosts hostconf: the api host routes a urlconf nothing else does, www the coverage tests' one."""

from django_hosts import host, patterns


host_patterns = patterns(
    "",
    host(r"api", "tests.django.apps.common.hosted_urls", name="api"),
    host(r"www", "tests.django.drf.test_coverage", name="www"),
)
