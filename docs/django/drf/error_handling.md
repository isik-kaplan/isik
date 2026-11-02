# error_handling

`drf_validation_error(e)` is the DRF `ValidationError` a Django one becomes: each field's messages with their codes, and the model's own - a constraint's, which Django keys `__all__` - under DRF's `NON_FIELD_ERRORS_KEY` (`non_field_errors` unless the project sets another), as a serializer's are. isik's `exception_handler` applies it to every view.

`django_to_drf_validation_error` is the same as a decorator, for code a project's own exception handler covers and isik's doesn't - a serializer method, a view - that calls into `model.full_clean()` or otherwise raises Django's `ValidationError`.

```python
from isik.django.drf.error_handling import django_to_drf_validation_error

@django_to_drf_validation_error
def save_widget(data):
    widget = Widget(**data)
    widget.full_clean()  # raises django.core.exceptions.ValidationError
    widget.save()
    return widget

# a field error becomes DRFValidationError(detail={"name": ["This field is required."]}), each message keeping its code
# a plain/list message, or the model's own (__all__), becomes DRFValidationError(detail={"non_field_errors": [...]})
```

- Any other exception type passes through untouched; the original Django `ValidationError` is preserved as `__cause__` on the re-raised DRF one.

## `exception_handler`

DRF renders an error with one message as `{"detail": ...}` alone, dropping the code it was raised with -
and a code is what a client tells two refusals apart by, not their wording. Set isik's handler, and each
carries its code beside its message, the shape isik's request policies and preconditions answer with:

```python
REST_FRAMEWORK = {"EXCEPTION_HANDLER": "isik.django.drf.error_handling.exception_handler"}

# 403  {"detail": "You do not have permission to perform this action.", "code": "permission_denied"}
# 403  {"detail": "Not yours.", "code": "not_owner"}        - PermissionDenied("Not yours.", code="not_owner")
# 404  {"detail": "No Widget matches the given query.", "code": "not_found"}
```

A validation error keeps DRF's shape, its fields' messages; anything that isn't an API error is left to
DRF as before. What headers a refusal carries is the project's to add.

Django's own `ValidationError` is a 400 in that shape too, not a 500. DRF never calls `full_clean()`, so a
row a `CheckConstraint` refuses is refused by `FullCleanOnSaveModel.save()` after the serializer passed - and
without this, that refusal escapes the view. A `UniqueConstraint` never got that far: DRF builds a validator
from one itself. With it, the constraint's `violation_error_message` is what the client reads:

```python
# 400  {"non_field_errors": ["A site has one primary domain."]}
```

