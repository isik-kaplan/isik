"""
`ISIK['DJANGO']['ACCOUNTS']`, the setting the accounts app reads - a dict whose keys each have a default:

    ISIK['DJANGO']['ACCOUNTS'] = {
        "DELETION_AFTER": timedelta(days=14),
        "DELETION_BACKEND": "isik.django.apps.accounts.backends.Anonymize",
        "ERASURES": "users.erasures",
    }

`DELETION_AFTER` is the project's to say - how long a person has to change their mind - and required while the app
is installed. Keys this module doesn't know are refused by a system check, `isik.E029`.
"""

from django.utils.module_loading import import_string

from isik.django.conf import django_area


DEFAULTS = {
    # How long after a person asks their account is deleted - signing back in before then keeps it. A timedelta.
    "DELETION_AFTER": None,
    # What deleting an account does: a backend class or its dotted path - Erase (isik's, unless set) or Anonymize,
    # in isik.django.apps.accounts.backends, or the project's own.
    "DELETION_BACKEND": "isik.django.apps.accounts.backends.Erase",
    # The module registering the project's models a person's rows are in, with erasure(); imported when it's ready.
    "ERASURES": None,
}


def configured():
    """`ISIK['DJANGO']['ACCOUNTS']` as the project set it."""
    area = django_area("ACCOUNTS")
    return area if isinstance(area, dict) else {}


def setting(name):
    """`name`'s value: the project's, else its default."""
    return configured().get(name, DEFAULTS[name])


def deletion_backend():
    """The deletion backend, made: `DELETION_BACKEND`, imported when it's a dotted path."""
    backend = setting("DELETION_BACKEND")
    return (import_string(backend) if isinstance(backend, str) else backend)()
