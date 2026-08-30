import pytest

from isik.common.utils.validation import validate_inputs


def is_positive(value):
    return value > 0


class TestValidateInputsPositional:
    def test_passes_when_the_validator_is_satisfied(self):
        @validate_inputs(is_positive)
        def thing(value):
            return value

        assert thing(5) == 5

    def test_raises_value_error_naming_the_argument_and_value(self):
        @validate_inputs(is_positive)
        def thing(value):
            return value

        with pytest.raises(ValueError, match=r"thing\(\) got an invalid value for 'value': -1"):
            thing(-1)

    def test_a_positional_arg_passed_by_keyword_is_still_validated(self):
        @validate_inputs(is_positive)
        def thing(value):
            return value

        with pytest.raises(ValueError, match="'value'"):
            thing(value=-1)

    def test_validators_line_up_with_positional_parameters_in_order(self):
        @validate_inputs(is_positive, is_positive)
        def thing(a, b):
            return a, b

        assert thing(1, 2) == (1, 2)
        with pytest.raises(ValueError, match="'b'"):
            thing(1, -2)

    def test_a_parameter_with_no_validator_is_unchecked(self):
        @validate_inputs(is_positive)
        def thing(value, other):
            return value, other

        assert thing(1, "anything at all") == (1, "anything at all")


class TestValidateInputsKeyword:
    def test_passes_when_the_validator_is_satisfied(self):
        @validate_inputs(unit=lambda u: u in ("cm", "in"))
        def resize(value, *, unit="cm"):
            return value, unit

        assert resize(10, unit="cm") == (10, "cm")

    def test_raises_value_error_naming_the_argument_and_value(self):
        @validate_inputs(unit=lambda u: u in ("cm", "in"))
        def resize(value, *, unit="cm"):
            return value, unit

        with pytest.raises(ValueError, match=r"resize\(\) got an invalid value for 'unit': 'km'"):
            resize(10, unit="km")

    def test_the_default_value_is_validated_when_the_caller_does_not_override_it(self):
        @validate_inputs(unit=lambda u: u in ("cm", "in"))
        def resize(value, *, unit="km"):
            return value, unit

        with pytest.raises(ValueError, match="'unit'"):
            resize(10)

    def test_positional_and_keyword_validators_combine(self):
        @validate_inputs(is_positive, unit=lambda u: u in ("cm", "in"))
        def resize(value, *, unit="cm"):
            return value, unit

        assert resize(10, unit="in") == (10, "in")
        with pytest.raises(ValueError, match="'value'"):
            resize(-10, unit="in")
        with pytest.raises(ValueError, match="'unit'"):
            resize(10, unit="km")


class TestValidateInputsValidatorRaisesItsOwn:
    def test_propagates_unchanged_instead_of_a_generic_value_error(self):
        def strict(value):
            if value < 0:
                raise TypeError("not a domain-specific ValueError at all")
            return True

        @validate_inputs(strict)
        def thing(value):
            return value

        with pytest.raises(TypeError, match="not a domain-specific ValueError at all"):
            thing(-1)


class TestValidateInputsSelfCls:
    def test_self_is_skipped_for_a_positional_validator(self):
        class Widget:
            @validate_inputs(is_positive)
            def resize(self, value):
                return value

        assert Widget().resize(5) == 5
        with pytest.raises(ValueError, match="'value'"):
            Widget().resize(-5)

    def test_cls_is_skipped_for_a_classmethod_validator(self):
        class Widget:
            @classmethod
            @validate_inputs(is_positive)
            def make(cls, value):
                return value

        assert Widget.make(5) == 5
        with pytest.raises(ValueError, match="'value'"):
            Widget.make(-5)

    def test_a_plain_function_named_value_first_is_not_mistaken_for_a_method(self):
        # The self/cls skip is name-based - a plain function whose first param isn't literally
        # named self/cls is never affected by it.
        @validate_inputs(is_positive)
        def thing(value):
            return value

        with pytest.raises(ValueError, match="'value'"):
            thing(-1)


class TestValidateInputsDecorationTimeErrors:
    def test_raises_when_more_positional_validators_than_positional_parameters(self):
        with pytest.raises(TypeError, match=r"only has 1 positional parameters \(self/cls excluded\)\."):

            @validate_inputs(is_positive, is_positive)
            def thing(value):
                return value

    def test_raises_when_a_keyword_validator_names_an_unknown_parameter(self):
        with pytest.raises(TypeError, match=r"\['nonexistent'\]"):

            @validate_inputs(nonexistent=is_positive)
            def thing(value):
                return value

    def test_positional_validators_do_not_count_self_against_the_limit(self):
        # Exactly one positional (non-self) parameter, exactly one positional validator - fine.
        class Widget:
            @validate_inputs(is_positive)
            def resize(self, value):
                return value

        assert Widget().resize(5) == 5
