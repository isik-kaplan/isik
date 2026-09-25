import importlib

from isik._internal.translation import gettext as _


def check_extra(extra_name, package_name):
    try:
        importlib.import_module(package_name)
    except ImportError as exception:
        raise ImportError(
            _(
                "The module you are trying to use requires '%(extra)s'. "
                "Please install it with: pip install isik[%(extra)s]"
            )
            % {"extra": extra_name}
        ) from exception
