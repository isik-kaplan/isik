"""project_app_configs() - the installed apps that are the project's own, not a library's."""

import os
from pathlib import Path

from django.apps import apps


_LIBRARIES = ("site-packages", "dist-packages")
_ISIK = str(Path(__file__).resolve().parents[3]) + os.sep


def is_library_path(path):
    """Whether `path` is a library's - installed as one (under `site-packages`/`dist-packages`), or isik's."""
    return any(part in _LIBRARIES for part in Path(path).parts) or str(Path(path).resolve()).startswith(_ISIK)


def project_app_configs(labels=None):
    """
    The app configs of the apps `labels` names, in the order given - or, by default, of every installed
    app that is the project's own: not installed as a library (under `site-packages`/`dist-packages`),
    and not isik's.
    """
    if labels is not None:
        return [apps.get_app_config(label) for label in labels]
    return [config for config in apps.get_app_configs() if not is_library_path(config.path)]
