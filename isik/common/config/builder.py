import os

from isik.common.config.exceptions import ConfigError
from isik.common.utils.functional import with_attrs


_MISSING = object()


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
            raise ConfigError(f"'{key}' is not a key in this config.") from None
        if isinstance(value, dict):
            self[key].refresh()
        else:
            self[key] = _read_leaf(value, [*self._path, key], self._root_schema, self._prefix, self._sep)


class Ref:
    """
    A `missing_default`/`error_default` that points at another key in the same schema
    instead of a static value - see `ref()`.
    """

    def __init__(self, *path):
        if not path:
            raise ConfigError("ref() requires at least one path segment.")
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
            raise ConfigError("ref() accepts either positional path segments or `dot=`, not both.")
        path = tuple(dot.split("."))
        if not all(path):
            raise ConfigError(f"ref(dot={dot!r}) is not a valid dotted path.")
    return Ref(*path)


def _environment_key(prefix, path, sep):
    return sep.join([*([prefix] if prefix else []), *path])


def _resolve_ref_caster(root_schema, path):
    node = root_schema
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(f"ref{tuple(path)} does not point to a key in this config.")
        node = node[key]
    if isinstance(node, dict):
        raise ConfigError(f"ref{tuple(path)} points to a nested config, not a single setting.")
    return node


def _fallback(caster, attr_name, root_schema, prefix, sep, seen, on_missing):
    default = getattr(caster, attr_name, _MISSING)
    if default is _MISSING:
        raise on_missing() from None

    if isinstance(default, Ref):
        if tuple(default.path) in seen:
            chain = " -> ".join(".".join(p) for p in [*seen, default.path])
            raise ConfigError(f"Circular ref() chain: {chain}.")
        ref_caster = _resolve_ref_caster(root_schema, default.path)
        return _read_leaf(ref_caster, list(default.path), root_schema, prefix, sep, seen)

    return default


def _read_leaf(caster, path, root_schema, prefix, sep, seen=frozenset()):
    seen = seen | {tuple(path)}
    environment_key = _environment_key(prefix, path, sep)

    try:
        raw_value = os.environ[environment_key]
    except KeyError:
        return _fallback(
            caster,
            "missing_default",
            root_schema,
            prefix,
            sep,
            seen,
            on_missing=lambda: ConfigError(
                f"Environment variable {environment_key} not found."
                f" Please set it or provide a `missing_default` to your caster."
            ),
        )

    try:
        return caster(raw_value)
    except Exception as exception:
        try:
            return _fallback(
                caster,
                "error_default",
                root_schema,
                prefix,
                sep,
                seen,
                on_missing=lambda: ConfigError(
                    f"Error while parsing {environment_key}={raw_value!r} with '{caster}'."
                    " Please check the value and the caster or provide an `error_default` to your caster."
                ),
            )
        except ConfigError as config_error:
            raise config_error from exception


def _build(data, path, prefix, sep, root_schema):
    result = Config()
    object.__setattr__(result, "_schema", data)
    object.__setattr__(result, "_path", path)
    object.__setattr__(result, "_prefix", prefix)
    object.__setattr__(result, "_sep", sep)
    object.__setattr__(result, "_root_schema", root_schema)
    for key, value in data.items():
        key_path = [*path, key]
        if isinstance(value, dict):
            result[key] = _build(value, key_path, prefix, sep, root_schema)
        elif callable(value):
            result[key] = _read_leaf(value, key_path, root_schema, prefix, sep)
        else:
            raise ConfigError(
                f"Values either must be callables or other mappings, not {type(value)}. Key={'.'.join(key_path)}."
            )
    return result


@with_attrs(ref=ref)
def config(data, *, prefix=None, sep="__"):
    return _build(data, [], prefix, sep, data)
