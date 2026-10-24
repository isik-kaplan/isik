"""
Reading one variable from an environment - directly as `KEY`, or from the file `KEY_FILE` names, the
way Docker and systemd hand secrets over. `config()` and `isik.common.settings` both read through it.
"""

from isik._internal.translation import gettext as _
from isik.common.config.exceptions import ConfigError


FILE_SUFFIX = "_FILE"


def read_file(path, *, name):
    """A file's text without its final line break - an editor or `echo` adds one nobody meant as part of the value."""
    try:
        # newline="": the value as written - a \r inside a secret is part of it, not a line ending to translate.
        with open(path, newline="") as file:
            text = file.read()
    except OSError as error:
        raise ConfigError(
            _("%(name)s names %(path)s, which can't be read: %(error)s")
            % {"name": name, "path": path, "error": error.strerror or error}
        ) from error
    return text.removesuffix("\n").removesuffix("\r")


def read_variable(environ, key, *, files=True):
    """
    `(text, from_file)` for `key`, or None when it isn't set. With `files`, `KEY_FILE` names a file to read
    the value from instead; setting both is refused rather than one silently winning.

        read_variable({"TOKEN_FILE": "/run/secrets/token"}, "TOKEN")   # (the file's text, True)
    """
    file_key = key + FILE_SUFFIX
    direct = key in environ
    from_file = files and file_key in environ
    if direct and from_file:
        raise ConfigError(
            _("%(key)s and %(file_key)s are both set - set only one of them.") % {"key": key, "file_key": file_key}
        )
    if from_file:
        return read_file(environ[file_key], name=file_key), True
    if direct:
        return environ[key], False
    return None
