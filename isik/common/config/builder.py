import os

from isik.common.config.exceptions import ConfigError


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
            self[key] = _read_leaf(value, _environment_key(self._prefix, [*self._path, key], self._sep))


def _environment_key(prefix, path, sep):
    return sep.join([*([prefix] if prefix else []), *path])


def _read_leaf(caster, environment_key):
    try:
        raw_value = os.environ[environment_key]
    except KeyError:
        default = getattr(caster, "missing_default", _MISSING)
        if default is _MISSING:
            raise ConfigError(
                f"Environment variable {environment_key} not found."
                f" Please set it or provide a `missing_default` to your caster."
            ) from None
        return default

    try:
        return caster(raw_value)
    except Exception as exception:
        default = getattr(caster, "error_default", _MISSING)
        if default is _MISSING:
            raise ConfigError(
                f"Error while parsing {environment_key}={raw_value!r} with '{caster}'."
                " Please check the value and the caster or provide an `error_default` to your caster."
            ) from exception
        return default


def _build(data, path, prefix, sep):
    result = Config()
    object.__setattr__(result, "_schema", data)
    object.__setattr__(result, "_path", path)
    object.__setattr__(result, "_prefix", prefix)
    object.__setattr__(result, "_sep", sep)
    for key, value in data.items():
        key_path = [*path, key]
        if isinstance(value, dict):
            result[key] = _build(value, key_path, prefix, sep)
        elif callable(value):
            result[key] = _read_leaf(value, _environment_key(prefix, key_path, sep))
        else:
            raise ConfigError(
                f"Values either must be callables or other mappings, not {type(value)}. Key={'.'.join(key_path)}."
            )
    return result


def config(data, *, prefix=None, sep="__"):
    return _build(data, [], prefix, sep)
