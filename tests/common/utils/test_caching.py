import pytest

from isik.common.utils.caching import get_cached


class TestGetCached:
    def test_computes_and_caches_on_first_access(self):
        calls = []

        class Obj:
            pass

        obj = Obj()

        def factory():
            calls.append(1)
            return "computed"

        assert get_cached(obj, "attr", factory) == "computed"
        assert get_cached(obj, "attr", factory) == "computed"
        assert len(calls) == 1

    def test_does_not_call_factory_when_attribute_exists(self):
        class Obj:
            pass

        obj = Obj()
        object.__setattr__(obj, "attr", "preset")

        assert get_cached(obj, "attr", lambda: pytest.fail("factory should not run")) == "preset"
