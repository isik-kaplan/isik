import threading
from contextvars import ContextVar

from isik.common.utils.concurrency import ContextLocal, ThreadLocal, ThreadLock


class _PopulateRegistryOnEnter:
    """A stand-in for threading.Lock that populates the registry as soon as it's acquired."""

    def __init__(self, populate):
        self.populate = populate

    def __enter__(self):
        self.populate()

    def __exit__(self, *args):
        return False


class TestThreadLocal:
    def test_same_name_returns_the_same_object(self):
        assert ThreadLocal("SAME_THREAD_LOCAL") is ThreadLocal("SAME_THREAD_LOCAL")

    def test_is_actually_thread_local(self):
        local = ThreadLocal("ISOLATION_THREAD_LOCAL")
        local.value = "main"
        seen = {}

        def worker():
            seen["value"] = getattr(local, "value", "unset")

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        assert local.value == "main"
        assert seen["value"] == "unset"

    def test_double_checked_lock_does_not_overwrite_a_concurrently_created_entry(self, monkeypatch):
        name = "RACE_THREAD_LOCAL"
        sentinel = object()
        monkeypatch.setattr(
            ThreadLocal,
            "_registry_lock",
            _PopulateRegistryOnEnter(lambda: ThreadLocal._registry.__setitem__(name, sentinel)),
        )
        assert ThreadLocal(name) is sentinel


class TestThreadLock:
    def test_same_name_returns_the_same_lock(self):
        assert ThreadLock("SAME_THREAD_LOCK") is ThreadLock("SAME_THREAD_LOCK")

    def test_is_a_real_lock(self):
        lock = ThreadLock("REAL_THREAD_LOCK")
        assert lock.acquire(blocking=False)
        lock.release()

    def test_double_checked_lock_does_not_overwrite_a_concurrently_created_entry(self, monkeypatch):
        name = "RACE_THREAD_LOCK"
        sentinel = object()
        monkeypatch.setattr(
            ThreadLock,
            "_registry_lock",
            _PopulateRegistryOnEnter(lambda: ThreadLock._registry.__setitem__(name, sentinel)),
        )
        assert ThreadLock(name) is sentinel


class TestContextLocal:
    def test_same_name_returns_the_same_instance(self):
        assert ContextLocal("SAME_CONTEXT_LOCAL") is ContextLocal("SAME_CONTEXT_LOCAL")

    def test_set_get_and_reset_roundtrip(self):
        local = ContextLocal("ROUNDTRIP_CONTEXT_LOCAL")
        token = local.set("key", "value")
        assert local.get("key") == "value"
        local.reset("key", token)
        assert local.get("key", "default") == "default"

    def test_get_default_when_never_set(self):
        local = ContextLocal("DEFAULT_CONTEXT_LOCAL")
        assert local.get("missing", "fallback") == "fallback"

    def test_double_checked_lock_does_not_overwrite_a_concurrently_created_entry(self, monkeypatch):
        name = "RACE_CONTEXT_LOCAL"
        sentinel = object()
        monkeypatch.setattr(
            ContextLocal,
            "_registry_lock",
            _PopulateRegistryOnEnter(lambda: ContextLocal._registry.__setitem__(name, sentinel)),
        )
        assert ContextLocal(name) is sentinel

    def test_get_var_double_checked_lock_does_not_overwrite_a_concurrently_created_var(self, monkeypatch):
        local = ContextLocal("RACE_CONTEXT_LOCAL_GET_VAR")
        vars_ = object.__getattribute__(local, "_vars")
        sentinel = ContextVar("sentinel")
        monkeypatch.setattr(
            local,
            "_vars_lock",
            _PopulateRegistryOnEnter(lambda: vars_.__setitem__("key", sentinel)),
        )
        assert local._get_var("key") is sentinel
