import pytest
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError

from isik.django.drf.error_handling import django_to_drf_validation_error


class TestDjangoToDrfValidationError:
    def test_transforms_a_field_validation_error_into_its_message_dict(self):
        @django_to_drf_validation_error
        def raise_field_error():
            raise ValidationError({"name": ["This field is required."]})

        with pytest.raises(DRFValidationError) as exc_info:
            raise_field_error()
        assert exc_info.value.detail == {"name": ["This field is required."]}

    def test_transforms_a_plain_message_error_into_non_field_errors(self):
        @django_to_drf_validation_error
        def raise_plain_error():
            raise ValidationError("Something went wrong")

        with pytest.raises(DRFValidationError) as exc_info:
            raise_plain_error()
        assert exc_info.value.detail == {"non_field_errors": ["Something went wrong"]}

    def test_transforms_a_list_of_messages_into_non_field_errors(self):
        @django_to_drf_validation_error
        def raise_list_error():
            raise ValidationError(["first error", "second error"])

        with pytest.raises(DRFValidationError) as exc_info:
            raise_list_error()
        assert exc_info.value.detail == {"non_field_errors": ["first error", "second error"]}

    def test_substitutes_params_into_the_message(self):
        @django_to_drf_validation_error
        def raise_error_with_params():
            raise ValidationError("Value must be at least %(limit)s", params={"limit": 5})

        with pytest.raises(DRFValidationError) as exc_info:
            raise_error_with_params()
        assert exc_info.value.detail == {"non_field_errors": ["Value must be at least 5"]}

    def test_leaves_other_exceptions_untouched(self):
        @django_to_drf_validation_error
        def raise_other():
            raise TypeError("boom")

        with pytest.raises(TypeError):
            raise_other()

    def test_chains_the_original_django_validation_error(self):
        @django_to_drf_validation_error
        def raise_field_error():
            raise ValidationError({"name": ["This field is required."]})

        try:
            raise_field_error()
        except DRFValidationError as exc:
            assert isinstance(exc.__cause__, ValidationError)
