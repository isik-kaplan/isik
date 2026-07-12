import pytest

from isik.common.utils.error_handling import SuppressAndRun, TransformExceptions, suppress_callable


class TestTransformExceptions:
    def test_transforms_a_caught_exception_type(self):
        with pytest.raises(KeyError):
            with TransformExceptions(ValueError, transform=lambda e: KeyError(str(e))):
                raise ValueError("boom")

    def test_leaves_other_exception_types_untouched(self):
        with pytest.raises(TypeError):
            with TransformExceptions(ValueError, transform=lambda e: KeyError(str(e))):
                raise TypeError("boom")

    def test_chains_the_original_exception_by_default(self):
        try:
            with TransformExceptions(ValueError, transform=lambda e: KeyError(str(e))):
                raise ValueError("boom")
        except KeyError as exc:
            assert isinstance(exc.__cause__, ValueError)

    def test_keep_original_false_drops_the_chain(self):
        try:
            with TransformExceptions(ValueError, transform=lambda e: KeyError(str(e)), keep_original=False):
                raise ValueError("boom")
        except KeyError as exc:
            assert exc.__cause__ is None

    def test_works_as_a_decorator(self):
        @TransformExceptions(ValueError, transform=lambda e: RuntimeError(str(e)))
        def parse(x):
            if x < 0:
                raise ValueError("negative")
            return x

        assert parse(1) == 1
        with pytest.raises(RuntimeError):
            parse(-1)

    def test_two_step_form_decorates_the_transform_then_the_guarded_function(self):
        @TransformExceptions(ValueError)
        def value_error_to_runtime_error(e):
            return RuntimeError(str(e))

        @value_error_to_runtime_error
        def parse(x):
            if x < 0:
                raise ValueError("negative")
            return x

        assert parse(1) == 1
        with pytest.raises(RuntimeError):
            parse(-1)

    def test_two_step_form_returns_the_same_instance_as_the_transform(self):
        wrapper = TransformExceptions(ValueError)

        @wrapper
        def value_error_to_runtime_error(e):
            return RuntimeError(str(e))

        assert value_error_to_runtime_error is wrapper
        assert wrapper.transform is not None

    def test_two_step_form_transform_is_reusable_on_multiple_functions(self):
        @TransformExceptions(ValueError)
        def value_error_to_runtime_error(e):
            return RuntimeError(str(e))

        @value_error_to_runtime_error
        def parse_a(x):
            raise ValueError("a")

        @value_error_to_runtime_error
        def parse_b(x):
            raise ValueError("b")

        with pytest.raises(RuntimeError):
            parse_a(1)
        with pytest.raises(RuntimeError):
            parse_b(1)

    def test_missing_transform_raises_a_clear_error_used_as_a_bare_context_manager(self):
        with pytest.raises(TypeError, match="no transform set"):
            with TransformExceptions(ValueError):
                raise ValueError("boom")


class TestSuppressAndRun:
    def test_suppresses_the_given_exception(self):
        with SuppressAndRun(ValueError):
            raise ValueError("boom")

    def test_calls_func_with_the_suppressed_exception(self):
        seen = []
        with SuppressAndRun(ValueError, func=seen.append):
            raise ValueError("boom")
        assert len(seen) == 1
        assert isinstance(seen[0], ValueError)

    def test_does_not_suppress_other_exception_types(self):
        with pytest.raises(TypeError):
            with SuppressAndRun(ValueError, func=lambda e: None):
                raise TypeError("boom")

    def test_func_is_not_called_when_nothing_is_suppressed(self):
        seen = []
        with SuppressAndRun(ValueError, func=seen.append):
            pass
        assert seen == []


class TestSuppressCallable:
    def test_suppresses_and_returns_none_by_default(self):
        @suppress_callable(ValueError)
        def parse(x):
            raise ValueError("boom")

        assert parse(1) is None

    def test_returns_the_given_static_value(self):
        @suppress_callable(ValueError, return_value=0)
        def parse(x):
            raise ValueError("boom")

        assert parse(1) == 0

    def test_calls_the_replacement_function(self):
        @suppress_callable(ValueError, return_func=lambda x: x * 0)
        def parse(x):
            raise ValueError("boom")

        assert parse(5) == 0

    def test_does_not_suppress_when_no_exception_is_raised(self):
        @suppress_callable(ValueError, return_value=0)
        def parse(x):
            return x

        assert parse(5) == 5

    def test_return_value_and_return_func_are_mutually_exclusive(self):
        with pytest.raises(ValueError):
            suppress_callable(ValueError, return_value=0, return_func=lambda x: x)

    def test_func_receives_the_suppressed_exception(self):
        seen = []

        @suppress_callable(ValueError, func=seen.append)
        def parse(x):
            raise ValueError("boom")

        parse(1)
        assert len(seen) == 1
