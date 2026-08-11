import threading
import uuid
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
        # A fresh name per run, not a fixed literal - _registry is process-global and this test
        # deliberately leaves an entry behind under it, so a fixed name would only pass the first
        # time this test runs in a given process (e.g. under a mutation-testing tool that
        # re-invokes pytest without restarting the process).
        name = f"RACE_THREAD_LOCAL_{uuid.uuid4().hex}"
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
        # A fresh name per run - see the matching ThreadLocal test's comment.
        name = f"RACE_THREAD_LOCK_{uuid.uuid4().hex}"
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
        # A fresh name per run - ContextLocal is a process-global singleton-by-name registry, and
        # _get_var() only actually builds a new ContextVar the first time a given (name, key)
        # combination is seen - a fixed name would silently skip that code path on a second
        # in-process run of this test (e.g. a mutation-testing tool re-invoking pytest without
        # restarting).
        local = ContextLocal(f"ROUNDTRIP_CONTEXT_LOCAL_{uuid.uuid4().hex}")
        token = local.set("key", "value")
        assert local.get("key") == "value"
        local.reset("key", token)
        assert local.get("key", "default") == "default"

    def test_get_default_when_never_set(self):
        local = ContextLocal("DEFAULT_CONTEXT_LOCAL")
        assert local.get("missing", "fallback") == "fallback"

    def test_get_var_names_the_contextvar_after_its_own_namespace_and_key(self):
        name = f"NAMED_CONTEXT_LOCAL_{uuid.uuid4().hex}"
        local = ContextLocal(name)
        assert local._get_var("key").name == f"{name}.key"

    def test_double_checked_lock_does_not_overwrite_a_concurrently_created_entry(self, monkeypatch):
        # A fresh name per run - see the matching ThreadLocal test's comment.
        name = f"RACE_CONTEXT_LOCAL_{uuid.uuid4().hex}"
        sentinel = object()
        monkeypatch.setattr(
            ContextLocal,
            "_registry_lock",
            _PopulateRegistryOnEnter(lambda: ContextLocal._registry.__setitem__(name, sentinel)),
        )
        assert ContextLocal(name) is sentinel

    def test_get_var_double_checked_lock_does_not_overwrite_a_concurrently_created_var(self, monkeypatch):
        # A fresh name per run too - local itself would otherwise be the stale instance from a
        # previous in-process run (same singleton-by-name registry), not a new one.
        local = ContextLocal(f"RACE_CONTEXT_LOCAL_GET_VAR_{uuid.uuid4().hex}")
        vars_ = object.__getattribute__(local, "_vars")
        sentinel = ContextVar("sentinel")
        monkeypatch.setattr(
            local,
            "_vars_lock",
            _PopulateRegistryOnEnter(lambda: vars_.__setitem__("key", sentinel)),
        )
        assert local._get_var("key") is sentinel
