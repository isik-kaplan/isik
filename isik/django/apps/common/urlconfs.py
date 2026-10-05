"""project_urlconfs() - every urlconf a project serves, django-hosts' included when it's installed."""

from django.conf import settings


def _host_urlconfs():
    try:
        from django_hosts.resolvers import get_host_patterns
    except ImportError:
        return []
    if not getattr(settings, "ROOT_HOSTCONF", None):
        return []
    return [host.urlconf for host in get_host_patterns()]


def project_urlconfs(urlconf=None):
    """
    The urlconfs to walk, each once, in order: `urlconf` when given (one, or several in a list or tuple),
    otherwise `ROOT_URLCONF` and, when django-hosts is installed and `ROOT_HOSTCONF` set, every host's.

        project_urlconfs()                     # ["config.urls", "config.urls.api", "config.urls.admin"]
        project_urlconfs("config.urls.api")    # ["config.urls.api"]
    """
    if urlconf is None:
        found = [getattr(settings, "ROOT_URLCONF", None), *_host_urlconfs()]
    elif isinstance(urlconf, list | tuple):
        found = list(urlconf)
    else:
        found = [urlconf]
    return list(dict.fromkeys(each for each in found if each is not None))
