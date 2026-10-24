import os
from typing import NamedTuple

from isik._internal.translation import gettext as _
from isik.common.config.environment import FILE_SUFFIX, read_variable
from isik.common.config.exceptions import ConfigError
from isik.common.utils.functional import with_attrs


_MISSING = object()


class _Reading(NamedTuple):
    """How a schema's values are read: the key's prefix and separator, where from, and whether `KEY_FILE` counts."""

    prefix: object
    sep: str
    environ: object
    files: bool

    def variables(self):
        # Read when the value is: refresh() sees what os.environ holds then, not at config() time.
        return os.environ if self.environ is None else self.environ


class Config(dict):
    """
    A dict that also allows attribute access, e.g. config.DATABASE.HOST, and can reload its
    values from the environment via refresh() - callable on the root config or on any nested
    one, refreshing just that subtree in place so existing references to it see the update too.

    A schema key that collides with a dict method name (items, keys, ...) or with refresh
    itself is only reachable via item access (config["refresh"]), not attribute access.
    """

    __setattr__ = dict.__setitem__

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def refresh(self, *path):
        """
        Re-read values from the environment, in place. With no arguments, refreshes every
        value under this node. With a path of key names, refreshes only that one nested
        value instead - e.g. config.refresh("DATABASE", "HOST") or, equivalently,
        config.DATABASE.refresh("HOST").
        """
        if not path:
            for key in self._schema:
                self._refresh_key(key)
            return self

        key, *rest = path
        if rest:
            self[key].refresh(*rest)
        else:
            self._refresh_key(key)
        return self

    def _refresh_key(self, key):
        try:
            value = self._schema[key]
        except KeyError:
            raise ConfigError(_("'%(key)s' is not a key in this config.") % {"key": key}) from None
        if isinstance(value, dict):
            self[key].refresh()
        else:
            self[key] = _read_leaf(value, [*self._path, key], self._root_schema, self._reading)


class Ref:
    """
    A `missing_default`/`error_default` that points at another key in the same schema
    instead of a static value - see `ref()`.
    """

    def __init__(self, *path):
        if not path:
            raise ConfigError(_("ref() requires at least one path segment."))
        self.path = path


def ref(*path, dot=None):
    """
    Use as a caster's `missing_default` or `error_default` to fall back to another setting
    in the same schema - read via that setting's own caster and environment variable -
    instead of a static value. The referenced setting can itself be missing and fall back
    further, including to another `ref()`, chaining any number of times.

    `path` is absolute from the root of the schema, the same way `Config.refresh(*path)`
    addresses a key - e.g. `ref("DATABASE", "PORT")`, or `ref("PAGE_SIZE")` for a top-level
    key. As a shorthand for a nested path, pass `dot="DATABASE.PORT"` instead - mutually
    exclusive with positional segments.

    Also reachable as `config.ref(...)`.

    Example:
        config({
            "PAGE_SIZE": integer(missing_default=100),
            "MAX_PAGE_SIZE": integer(missing_default=config.ref("PAGE_SIZE")),
            "DRF": {"MAX_PAGE_SIZE": integer(missing_default=config.ref(dot="PAGE_SIZE"))},
        })
        # MAX_PAGE_SIZE falls back to whatever PAGE_SIZE resolves to when its own
        # environment variable isn't set.
    """
    if dot is not None:
        if path:
            raise ConfigError(_("ref() accepts either positional path segments or `dot=`, not both."))
        path = tuple(dot.split("."))
        if not all(path):
            raise ConfigError(_("ref(dot=%(dot)r) is not a valid dotted path.") % {"dot": dot})
    return Ref(*path)


def _environment_key(prefix, path, sep):
    return sep.join([*([prefix] if prefix else []), *path])


def _resolve_ref_caster(root_schema, path):
    node = root_schema
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(_("ref%(path)s does not point to a key in this config.") % {"path": tuple(path)})
        node = node[key]
    if isinstance(node, dict):
        raise ConfigError(_("ref%(path)s points to a nested config, not a single setting.") % {"path": tuple(path)})
    return node


def _fallback(caster, attr_name, root_schema, reading, seen, on_missing):
    default = getattr(caster, attr_name, _MISSING)
    if default is _MISSING:
        raise on_missing() from None

    if isinstance(default, Ref):
        if tuple(default.path) in seen:
            chain = " -> ".join(".".join(p) for p in [*seen, default.path])
            raise ConfigError(_("Circular ref() chain: %(chain)s.") % {"chain": chain})
        ref_caster = _resolve_ref_caster(root_schema, default.path)
        return _read_leaf(ref_caster, list(default.path), root_schema, reading, seen)

    return default


def _caster_name(caster):
    return getattr(caster, "__qualname__", None) or repr(caster)


def _read_leaf(caster, path, root_schema, reading, seen=frozenset()):
    seen = seen | {tuple(path)}
    environment_key = _environment_key(reading.prefix, path, reading.sep)

    found = read_variable(reading.variables(), environment_key, files=reading.files)
    if found is None:
        return _fallback(
            caster,
            "missing_default",
            root_schema,
            reading,
            seen,
            on_missing=lambda: ConfigError(
                _(
                    "Environment variable %(key)s not found."
                    " Please set it or provide a `missing_default` to your caster."
                )
                % {"key": environment_key}
            ),
        )

    raw_value, _from_file = found
    try:
        return caster(raw_value)
    except Exception as exception:
        try:
            return _fallback(
                caster,
                "error_default",
                root_schema,
                reading,
                seen,
                on_missing=lambda: ConfigError(
                    # The value stays out: a caster over a secret would put it in logs and Sentry
                    # the first time it's malformed. The caster's own error is the cause below.
                    _(
                        "Error while parsing %(key)s with '%(caster)s'."
                        " Please check the value and the caster or provide an `error_default` to your caster."
                    )
                    % {"key": environment_key, "caster": _caster_name(caster)}
                ),
            )
        except ConfigError as config_error:
            raise config_error from exception


def _build(data, path, reading, root_schema):
    result = Config()
    object.__setattr__(result, "_schema", data)
    object.__setattr__(result, "_path", path)
    object.__setattr__(result, "_reading", reading)
    object.__setattr__(result, "_root_schema", root_schema)
    for key, value in data.items():
        key_path = [*path, key]
        if isinstance(value, dict):
            result[key] = _build(value, key_path, reading, root_schema)
        elif callable(value):
            result[key] = _read_leaf(value, key_path, root_schema, reading)
        else:
            raise ConfigError(
                _("Values either must be callables or other mappings, not %(type)s. Key=%(key)s.")
                % {"type": type(value), "key": ".".join(key_path)}
            )
    return result


def _file_clashes(data, path, reading):
    # With files on, a KEY_FILE setting would also be read as KEY's file - two meanings for one variable.
    for key, value in data.items():
        if isinstance(value, dict):
            yield from _file_clashes(value, [*path, key], reading)
        elif key.endswith(FILE_SUFFIX) and key.removesuffix(FILE_SUFFIX) in data:
            yield _environment_key(reading.prefix, [*path, key], reading.sep)


@with_attrs(ref=ref)
def config(data, *, prefix=None, sep="__", environ=None, files=False):
    """
    Builds a `Config` from `data`, reading each leaf from the environment - see the module docs.

    `environ` reads from that mapping instead of `os.environ`. `files=True` also reads `KEY_FILE`: the
    path of a file holding KEY's value. It's off by default, since a schema may already have a setting
    of that name.
    """
    reading = _Reading(prefix, sep, environ, files)
    if files:
        clashes = sorted(_file_clashes(data, [], reading))
        if clashes:
            raise ConfigError(
                _("With files=True, %(keys)s would be read as another setting's file - rename it.")
                % {"keys": ", ".join(clashes)}
            )
    return _build(data, [], reading, data)
