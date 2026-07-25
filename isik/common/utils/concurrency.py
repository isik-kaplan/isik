import threading
from contextvars import ContextVar


class ThreadLocal:
    """
    A factory for thread local identified by name.
    Calling ThreadLocal("FOO") returns a thread local object shared across all calls with the same name.
    """

    _registry = {}
    _registry_lock = threading.Lock()

    def __new__(cls, name):
        if name not in cls._registry:
            with cls._registry_lock:
                if name not in cls._registry:
                    cls._registry[name] = threading.local()
        return cls._registry[name]


class ThreadLock:
    """
    A factory for thread lock identified by name.
    Calling ThreadLock("FOO") returns a lock object shared across all calls with the same name.
    """

    _registry = {}
    _registry_lock = threading.Lock()

    def __new__(cls, name):
        if name not in cls._registry:
            with cls._registry_lock:
                if name not in cls._registry:
                    cls._registry[name] = threading.Lock()
        return cls._registry[name]


class ContextLocal:
    """
    A named, registry-backed namespace of ContextVars.
    Calling ContextLocal("FOO") returns the same instance across all calls with the same name,
    making it safe for async and coroutine contexts.

    Usage:
        local = ContextLocal("MY_NAMESPACE")
        token = local.set("foo", "bar")
        local.get("foo")  # "bar"
        local.get("foo", "default")  # "bar"
        local.reset("foo", token)
    """

    _registry = {}
    _registry_lock = threading.Lock()

    def __new__(cls, name):
        if name not in cls._registry:
            with cls._registry_lock:
                if name not in cls._registry:
                    instance = super().__new__(cls)
                    object.__setattr__(instance, "_name", name)
                    object.__setattr__(instance, "_vars", {})
                    object.__setattr__(instance, "_vars_lock", threading.Lock())
                    cls._registry[name] = instance
        return cls._registry[name]

    def _get_var(self, key):
        vars_ = object.__getattribute__(self, "_vars")
        if key not in vars_:
            lock = object.__getattribute__(self, "_vars_lock")
            with lock:
                if key not in vars_:
                    name = object.__getattribute__(self, "_name")
                    vars_[key] = ContextVar(f"{name}.{key}")
        return vars_[key]

    def get(self, key, *args):
        """Get a value. Accepts an optional default as a second argument, like dict.get()."""
        return self._get_var(key).get(*args)

    def set(self, key, value):
        """Set a value and return a token that can be used to restore the previous state."""
        return self._get_var(key).set(value)

    def reset(self, key, token):
        """Reset a value to its state before the corresponding set() call."""
        self._get_var(key).reset(token)
