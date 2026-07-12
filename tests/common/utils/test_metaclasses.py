import pytest
from hypothesis import given
from hypothesis import strategies as st

from isik.common.utils.metaclasses import is_dunder, transform


class TestIsDunder:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("__init__", True),
            ("__abstract__", True),
            ("__transform__", True),
            ("_private", False),
            ("plain", False),
            ("__", False),
            ("____", False),
        ],
    )
    def test_known_cases(self, name, expected):
        assert is_dunder(name) is expected


def _make_doubled():
    class Doubled(metaclass=transform):
        __checks__ = staticmethod(lambda key, value, classdict: isinstance(value, int))
        __transform__ = staticmethod(lambda key, value, classdict: value * 2)

        one = 1
        two = 2

    return Doubled


def test_transform_is_applied_to_class_body_values_that_pass_checks():
    Doubled = _make_doubled()
    assert Doubled.one == 2
    assert Doubled.two == 4


def test_transform_is_applied_to_later_attribute_assignments():
    Doubled = _make_doubled()
    Doubled.two = 10
    assert Doubled.two == 20


def test_values_that_fail_checks_are_left_untouched():
    class Selective(metaclass=transform):
        __checks__ = staticmethod(lambda key, value, classdict: isinstance(value, int))
        __transform__ = staticmethod(lambda key, value, classdict: value * 2)

        number = 3
        text = "untouched"

    assert Selective.number == 6
    assert Selective.text == "untouched"


def test_setattr_leaves_values_that_fail_checks_untouched():
    class Selective(metaclass=transform):
        __checks__ = staticmethod(lambda key, value, classdict: isinstance(value, int))
        __transform__ = staticmethod(lambda key, value, classdict: value * 2)

        text = "before"

    Selective.text = "after"
    assert Selective.text == "after"


def test_defaults_to_a_plain_class_without_hooks():
    class Plain(metaclass=transform):
        value = "unchanged"

    assert Plain.value == "unchanged"
    Plain.value = "still unchanged"
    assert Plain.value == "still unchanged"


def test_abstract_blocks_instantiation():
    class NoInstances(metaclass=transform):
        __abstract__ = True

    with pytest.raises(TypeError, match="can not be instantiated"):
        NoInstances()


def test_without_abstract_instantiation_works_normally():
    class Instantiable(metaclass=transform):
        pass

    assert isinstance(Instantiable(), Instantiable)


@given(st.integers(min_value=-1000, max_value=1000))
def test_doubled_transform_holds_for_arbitrary_integers(value):
    class Doubled(metaclass=transform):
        __checks__ = staticmethod(lambda key, value, classdict: isinstance(value, int))
        __transform__ = staticmethod(lambda key, value, classdict: value * 2)

        n = value

    assert Doubled.n == value * 2
