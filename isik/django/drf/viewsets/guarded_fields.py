from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import OperandHolder, SingleOperandHolder

from isik.django.drf.permissions import ANY_VALUE, Guard


_MISSING = object()


def _nested_guards(permission):
    """Every Guard buried inside an `&`/`|`/`~` composition - where no viewset can find it."""
    if isinstance(permission, SingleOperandHolder):
        return _guards_in(permission.op1_class)
    if isinstance(permission, OperandHolder):
        return _guards_in(permission.op1_class) + _guards_in(permission.op2_class)
    return []


def _guards_in(permission):
    if isinstance(permission, type) and issubclass(permission, Guard):
        return [permission]
    return _nested_guards(permission)


def _dig(value, attrs):
    for attr in attrs:
        if isinstance(value, dict):
            value = value.get(attr, _MISSING)
        else:
            value = getattr(value, attr, _MISSING)
        if value is _MISSING:
            return _MISSING
    return value


def _changed(stored, incoming):
    if hasattr(stored, "all"):  # a related manager - compare the rows, not the manager
        return set(stored.all()) != set(incoming)
    if isinstance(incoming, dict):  # a nested serializer's payload - no stored value to compare it to
        return True
    return stored != incoming


class GuardedFieldsMixin:
    """
    Runs the `fields=` half of `guarding()` (see `isik.django.drf.permissions.guarding`): after a
    write validates, a field guard whose fields the write actually changes has its predicate
    evaluated against the stored row, and refuses with a `ValidationError` keyed by each such field.

    "Changes" means the typed, validated value differs from the stored one - so a client echoing
    a field back unchanged while editing its neighbours isn't refused. On create there is no stored
    row, so every field present counts. A dict-form guard (`fields={"names_issuer": True}`) only
    applies when the incoming value is its target.

    Hooks `perform_create`/`perform_update`. A handler that saves a serializer without going through
    them has to call `self.check_guarded_fields(serializer)` itself, after `is_valid()`.

    Guards must be top-level `permission_classes` entries - one nested inside `&`/`|`/`~` fails at
    class-definition time, since it would never run.
    """

    runs_field_guards = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        nested = [guard for entry in getattr(cls, "permission_classes", []) for guard in _nested_guards(entry)]
        if nested:
            raise ImproperlyConfigured(
                f"{cls.__name__} composes {nested[0].__name__} with &, | or ~ - a guard must be its own "
                "permission_classes entry, with any composition inside guarding()."
            )

    def get_field_guards(self):
        return [
            permission
            for permission in self.get_permissions()
            if isinstance(permission, Guard) and permission.fields is not None
        ]

    def check_guarded_fields(self, serializer):
        instance = serializer.instance
        errors = {}
        for guard in self.get_field_guards():
            touched = []
            for name, target in guard.fields.items():
                if name not in serializer.fields:
                    raise ImproperlyConfigured(
                        f"{type(guard).__name__} guards {name!r}, which {type(serializer).__name__} has no field named."
                    )
                field = serializer.fields[name]
                if field.read_only:
                    continue
                incoming = _dig(serializer.validated_data, field.source_attrs)
                if incoming is _MISSING:
                    continue
                if target is not ANY_VALUE and incoming != target:
                    continue
                if instance is not None and not _changed(_dig(instance, field.source_attrs), incoming):
                    continue
                touched.append(name)
            if touched and not guard.allows(self.request, self, instance):
                for name in touched:
                    errors.setdefault(name, []).append(guard.message)
        if errors:
            raise ValidationError(errors, code="permission_denied")

    def perform_create(self, serializer):
        self.check_guarded_fields(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self.check_guarded_fields(serializer)
        super().perform_update(serializer)
