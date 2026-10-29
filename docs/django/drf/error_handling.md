# error_handling

`django_to_drf_validation_error` is a decorator that catches Django's `ValidationError` and re-raises it as DRF's, so it comes back as a proper 400 response instead of falling through to Django's own error handling. Use it on anything DRF-adjacent (a serializer method, a view) that calls into `model.full_clean()` or otherwise raises Django's `ValidationError`.

```python
from isik.django.drf.error_handling import django_to_drf_validation_error

@django_to_drf_validation_error
def save_widget(data):
    widget = Widget(**data)
    widget.full_clean()  # raises django.core.exceptions.ValidationError
    widget.save()
    return widget

# a field error's message_dict becomes DRFValidationError(detail={"name": ["This field is required."]})
# a plain/list message becomes DRFValidationError(detail={"non_field_errors": [...]})
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
