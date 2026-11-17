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

A validation error carries each message's code the same way: every message in it is `{"message", "code"}`,
where DRF puts the message - a field's list, `non_field_errors`, a nested or `many=True` serializer's, a bare
list - as DRF's `get_full_details()` has it, and always in a list, a message DRF holds alone
(`ValidationError({"city": "Unknown."})`) too:

```python
# 400  {"email": [{"message": "Enter a valid email address.", "code": "invalid"}]}
# 400  {"items": [{}, {"qty": [{"message": "Ensure this value is less than or equal to 5.", "code": "max_value"}]}]}
# 400  {"non_field_errors": [{"message": "Pick one.", "code": "one"}]}
```

A validation error raised as one message - `ValidationError({"detail": "Disable it first."},
code="disable_first")`, a refusal that names no field - is answered as one, the shape above:
`{"detail": "Disable it first.", "code": "disable_first"}`.

Every value in a validation error is a message: data carried in one - an id, a reason - comes out as
`{"message": "<the id>", "code": "invalid"}`. A refusal carrying data is `refusal()`'s, below.

A code is a stable identifier, part of the API: a client branches on it, and the message, translated and
reworded freely, is what a person reads. isik's own codes change only with a breaking release, named in the
changelog. Anything that isn't an API error is left to DRF as before. What headers a refusal carries is the
project's to add.

Django's own `ValidationError` is a 400 in that shape too, not a 500. DRF never calls `full_clean()`, so a
row a `CheckConstraint` refuses is refused by `FullCleanOnSaveModel.save()` after the serializer passed - and
without this, that refusal escapes the view. A `UniqueConstraint` never got that far: DRF builds a validator
from one itself. With it, the constraint's `violation_error_message` is what the client reads, and its
`violation_error_code` the code - `invalid` when it sets none:

```python
# 400  {"non_field_errors": [{"message": "A site has one primary domain.", "code": "one_primary_domain"}]}
```

## `refusal`

`refusal(message, *, code, status=None, **data)` is a request refused - raised, so the transaction it's in
rolls back - its body `{"detail", "code"}` and `data` beside them, each value as it was given, whatever
exception handler a project sets. 409 unless `status` says otherwise:

```python
from isik.django.drf.error_handling import refusal

raise refusal(_("This subscription is forced by staff, so it can't be changed."), code="subscription_forced")
# 409  {"detail": "This subscription is forced by staff, so it can't be changed.", "code": "subscription_forced"}

raise refusal(
    _("Some organizations refused you."), code="join_refused", status=400,
    refused=[{"organization": str(pk), "reason": "schema_is_gone"}],
)
# 400  {"refused": [{"organization": "01a1...", "reason": "schema_is_gone"}],
#       "detail": "Some organizations refused you.", "code": "join_refused"}
```

`detail` and `code` are always the message and code given; data under either name is dropped.
