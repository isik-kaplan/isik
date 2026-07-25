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
