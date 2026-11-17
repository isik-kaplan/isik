import subprocess
import sys

import pytest
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.http import Http404
from rest_framework.exceptions import ErrorDetail, NotAuthenticated, PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError

from isik.django.drf.error_handling import django_to_drf_validation_error, exception_handler, refusal


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

    def test_keeps_each_messages_code(self):
        @django_to_drf_validation_error
        def raise_coded():
            raise ValidationError({"name": [ValidationError("Taken.", code="taken")]})

        with pytest.raises(DRFValidationError) as exc_info:
            raise_coded()
        assert exc_info.value.detail["name"][0].code == "taken"

    def test_a_models_own_error_is_under_drfs_non_field_key(self):
        @django_to_drf_validation_error
        def raise_model_wide():
            raise ValidationError({NON_FIELD_ERRORS: ["A site has one primary domain."]})

        with pytest.raises(DRFValidationError) as exc_info:
            raise_model_wide()
        assert exc_info.value.detail == {"non_field_errors": ["A site has one primary domain."]}

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


class TestTheExceptionHandler:
    @pytest.mark.parametrize(
        ("raised", "status_code", "code"),
        [
            (PermissionDenied(), 403, "permission_denied"),
            (PermissionDenied("Not yours.", code="not_owner"), 403, "not_owner"),
            (NotAuthenticated(), 401, "not_authenticated"),
            (Http404("No Widget matches the given query."), 404, "not_found"),
        ],
    )
    def test_a_single_message_carries_its_code(self, raised, status_code, code):
        response = exception_handler(raised, {})

        assert response.status_code == status_code
        assert set(response.data) == {"detail", "code"}
        assert response.data["code"] == code

    def test_a_validation_errors_messages_carry_their_codes(self):
        raised = DRFValidationError({"name": [ErrorDetail("This field is required.", code="required")]})

        response = exception_handler(raised, {})

        assert response.status_code == 400
        assert response.data == {"name": [{"message": "This field is required.", "code": "required"}]}

    def test_every_message_is_coded_however_deep(self):
        raised = DRFValidationError(
            {
                "non_field_errors": [ErrorDetail("Pick one.", code="one")],
                "address": {"city": [ErrorDetail("Unknown.", code="unknown")]},
                "items": [{}, {"qty": [ErrorDetail("Too many.", code="max_value")]}],
            }
        )

        assert exception_handler(raised, {}).data == {
            "non_field_errors": [{"message": "Pick one.", "code": "one"}],
            "address": {"city": [{"message": "Unknown.", "code": "unknown"}]},
            "items": [{}, {"qty": [{"message": "Too many.", "code": "max_value"}]}],
        }

    def test_a_validation_error_raised_as_one_message_is_answered_as_one(self):
        raised = DRFValidationError({"detail": "Disable it first."}, code="disable_first")

        response = exception_handler(raised, {})

        assert response.status_code == 400
        assert response.data == {"detail": "Disable it first.", "code": "disable_first"}

    def test_a_field_called_detail_is_a_field(self):
        raised = DRFValidationError({"detail": [ErrorDetail("Too long.", code="max_length")]})

        assert exception_handler(raised, {}).data == {"detail": [{"message": "Too long.", "code": "max_length"}]}

    def test_a_message_held_alone_is_listed_however_deep(self):
        raised = DRFValidationError({"city": "Unknown.", "address": {"street": ErrorDetail("Long.", code="long")}})

        assert exception_handler(raised, {}).data == {
            "city": [{"message": "Unknown.", "code": "invalid"}],
            "address": {"street": [{"message": "Long.", "code": "long"}]},
        }

    def test_a_list_of_lists_is_coded_in_each(self):
        raised = DRFValidationError({"grid": [[ErrorDetail("Bad.", code="bad")]]})

        assert exception_handler(raised, {}).data == {"grid": [[{"message": "Bad.", "code": "bad"}]]}

    def test_a_validation_error_raised_bare_is_a_coded_list(self):
        assert exception_handler(DRFValidationError("Nope.", code="nope"), {}).data == [
            {"message": "Nope.", "code": "nope"}
        ]

    def test_a_django_validation_error_is_a_400_in_drfs_shape_with_its_codes(self):
        raised = ValidationError({"name": [ValidationError("Taken.", code="taken")]})

        response = exception_handler(raised, {})

        assert response.status_code == 400
        assert response.data == {"name": [{"message": "Taken.", "code": "taken"}]}

    def test_a_models_own_error_is_under_drfs_non_field_key(self):
        raised = ValidationError({NON_FIELD_ERRORS: [ValidationError("A site has one primary domain.", code="one")]})

        response = exception_handler(raised, {})

        assert response.status_code == 400
        assert response.data == {"non_field_errors": [{"message": "A site has one primary domain.", "code": "one"}]}

    def test_a_models_own_errors_join_any_already_under_the_key(self):
        raised = ValidationError({"non_field_errors": ["first"], NON_FIELD_ERRORS: ["second"]})

        assert exception_handler(raised, {}).data == {
            "non_field_errors": [{"message": "first", "code": "invalid"}, {"message": "second", "code": "invalid"}]
        }

    def test_the_key_is_the_one_the_project_set(self, settings):
        settings.REST_FRAMEWORK = {**getattr(settings, "REST_FRAMEWORK", {}), "NON_FIELD_ERRORS_KEY": "errors"}

        assert exception_handler(ValidationError("Nope."), {}).data == {
            "errors": [{"message": "Nope.", "code": "invalid"}]
        }

    @pytest.mark.django_db
    def test_a_refused_constraint_reaches_the_client_as_its_message(self):
        from tests.testapp.models import CleanedNote

        with pytest.raises(ValidationError) as refused:
            CleanedNote(count=1, kind="screw").validate_constraints()
        response = exception_handler(refused.value, {})

        assert response.status_code == 400
        assert response.data == {
            "non_field_errors": [{"message": "kind must be one of: bolt, nut.", "code": "invalid_choice"}]
        }

    def test_what_isnt_an_api_error_is_left_alone(self):
        assert exception_handler(ValueError("boom"), {}) is None

    def test_a_detail_that_isnt_a_message_is_left_alone(self):
        raised = PermissionDenied()
        raised.detail = {"detail": ["one", "two"]}

        assert exception_handler(raised, {}).data == {"detail": ["one", "two"]}

    def test_drfs_handler_gets_the_context(self, monkeypatch):
        from rest_framework import views

        seen = []
        monkeypatch.setattr(views, "exception_handler", lambda exc, context: seen.append(context))
        context = {"view": "a view"}

        exception_handler(PermissionDenied(), context)

        assert seen == [context]


def test_drfs_views_load_with_an_isik_permission_by_default():
    # rest_framework.views loads the default permissions while it loads; an isik one loads isik.django.drf.
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "import django; from django.conf import settings; "
            "settings.configure(INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth', "
            "'rest_framework'], REST_FRAMEWORK={'DEFAULT_PERMISSION_CLASSES': "
            "['isik.django.drf.permissions.SignedInPermission']}); django.setup(); import rest_framework.views",
        ],
        capture_output=True,
        text=True,
    )

    assert run.returncode == 0, run.stderr


class TestARefusal:
    def test_is_a_409_with_its_message_and_code(self):
        response = exception_handler(refusal("Forced by staff.", code="subscription_forced"), {})

        assert response.status_code == 409
        assert response.data == {"detail": "Forced by staff.", "code": "subscription_forced"}

    def test_carries_its_data_as_given(self):
        refused = [{"organization": "01a1e7c6-12ff-7025-ada2-0e594bc98577", "reason": "schema_is_gone", "tries": 3}]

        response = exception_handler(
            refusal("Some organizations refused you.", code="join_refused", status=400, refused=refused), {}
        )

        assert response.status_code == 400
        assert response.data == {
            "refused": refused,
            "detail": "Some organizations refused you.",
            "code": "join_refused",
        }
        assert type(response.data["refused"][0]["organization"]) is str

    def test_its_message_and_code_are_its_own_whatever_the_data_says(self):
        response = exception_handler(refusal("Mine.", code="mine", detail="theirs"), {})

        assert response.data == {"detail": "Mine.", "code": "mine"}
