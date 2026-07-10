import importlib


def check_extra(extra_name, package_name):
    try:
        importlib.import_module(package_name)
    except ImportError as exception:
        raise ImportError(
            f"The module you are trying to use requires '{extra_name}'. "
            f"Please install it with: pip install isik[{extra_name}]"
        ) from exception
