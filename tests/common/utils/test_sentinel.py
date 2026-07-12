from isik.common.utils.sentinel import Sentinel


class TestSentinel:
    def test_same_name_returns_the_same_instance(self):
        assert Sentinel("FOO") is Sentinel("FOO")

    def test_different_names_are_different_instances(self):
        assert Sentinel("FOO_UNIQUE_A") is not Sentinel("FOO_UNIQUE_B")

    def test_is_always_falsy(self):
        assert not Sentinel("FALSY_SENTINEL")

    def test_repr_and_str_include_the_name(self):
        sentinel = Sentinel("REPR_SENTINEL")
        assert "REPR_SENTINEL" in repr(sentinel)
        assert "REPR_SENTINEL" in str(sentinel)

    def test_equality_and_hash_are_identity_based(self):
        same = Sentinel("EQ_SENTINEL")
        other = Sentinel("EQ_SENTINEL_OTHER")
        assert Sentinel("EQ_SENTINEL") == same
        assert Sentinel("EQ_SENTINEL") != other
        assert hash(Sentinel("EQ_SENTINEL")) == hash(same)
