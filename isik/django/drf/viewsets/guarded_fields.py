from contextlib import ExitStack

from django.core.exceptions import ImproperlyConfigured
from django.db import connections, transaction
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import SAFE_METHODS, OperandHolder, SingleOperandHolder
from rest_framework.serializers import ListSerializer

from isik._internal.reasons import require_exemption, require_reasons
from isik._internal.translation import gettext as _
from isik.common.utils.exemptions import Exemption
from isik.django.drf._changes import MISSING as _MISSING
from isik.django.drf._changes import changed as _changed
from isik.django.drf._changes import dig as _dig
from isik.django.drf.permissions import Guard
from isik.django.drf.serializers.guarded_save import FieldGuardsOnSaveMixin, _active_guarded_view


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


class WritesNoGuardedFields(
    Exemption,
    rule="isik.guarded-fields.writes-none",
    why="A write through a viewset with field guards runs them, or it fails and is rolled back.",
):
    """Why an action writes no guarded field - `@writes_no_guarded_fields`'s type."""


def writes_no_guarded_fields(reason):
    """
    Marks a viewset action as writing no guarded field - one that saves through no serializer at
    all (a service call, an ORM update), so its viewset's field guards have nothing to check - and
    says why, in at least 40 characters (or as a `WritesNoGuardedFields(reason=...)`):

        @writes_no_guarded_fields("rotates the secret through the service, touching no guarded field")
        @action(detail=True, methods=["post"])
        def rotate_secret(self, request, pk=None):
            ...

    Without it, a successful unsafe request through a viewset with field guards that never ran them
    fails - and is rolled back - rather than trusting that nothing guarded was written. Either
    decorator order works.
    """
    if not isinstance(reason, str):
        raise ImproperlyConfigured(
            _(
                "writes_no_guarded_fields takes the reason the action writes no guarded field - "
                '@writes_no_guarded_fields("..."), not %(value)r.'
            )
            % {"value": reason}
        )
    reason = require_exemption("writes_no_guarded_fields", "reason", reason, WritesNoGuardedFields)

    def mark(handler):
        handler.writes_no_guarded_fields = reason
        return handler

    return mark


class GuardedFieldsMixin:
    """
    Runs the `fields=`/`setting=` guards of `guarding()` (see `isik.django.drf.permissions.guarding`): after a
    write validates, a field guard whose fields the write actually changes has its predicate
    evaluated against the stored row, and refuses with a `ValidationError` keyed by each such field.

    "Changes" means the typed, validated value differs from the stored one - so a client echoing
    a field back unchanged while editing its neighbors isn't refused. It's about what the write
    does, however the value arrives: the payload, a serializer default, or a HiddenField like
    CurrentUserField all count. On create there is no stored row, so every value supplied counts.
    Fields this write can't reach - read-only, dropped by `?only=`/`?exclude=`, absent from the
    action's serializer - are skipped. A `setting=` guard (`setting={"names_issuer": True}`) only
    applies when the incoming value matches its target (`guarding.values(...)` for several).

    Where it runs, so no write path can skip it:

    - Inside `save()` of any serializer with `FieldGuardsOnSaveMixin` (every `BaseModelSerializer`),
      before the write - however the serializer was built, and counting `save(**kwargs)` values.
    - In `perform_create`/`perform_update`, for a serializer without that mixin.
    - Anything else - a plain DRF serializer saved by hand, an ORM write in a custom action - is caught
      after the fact: an unsafe request that succeeds without its guards having run raises
      `ImproperlyConfigured`, inside a transaction on every database (`field_guard_databases` narrows
      it), so its database writes roll back. Call `self.check_guarded_fields(serializer)` yourself,
      or mark an action that writes no guarded field `@writes_no_guarded_fields("<why>")`. `destroy`
      writes no fields and is exempt (`actions_writing_no_guarded_fields`, `{action: reason}`). Side
      effects outside the database aren't rolled back - which is what the loud failure is for: a test
      hitting the endpoint finds it.

    The transaction and the after-the-fact check apply to viewsets whose `permission_classes` declare
    a field guard.

    Guards must be top-level `permission_classes` entries - one nested inside `&`/`|`/`~` fails at
    class-definition time, since it would never run.

    A guard binds to the endpoint, not the field - so two viewsets sharing a serializer where only one
    guards a field would leave it open through the other. That fails at class-definition time too,
    naming both. A viewset that means to leave the field open says so with `unguarded_fields`:

        class PlatformInvitationViewSet(BaseModelViewSet):
            serializer_class = PublicInvitationSerializer  # also used by the tenant viewset
            unguarded_fields = {"names_issuer": "platform staff may change who an invitation names"}

    Only viewsets with this mixin (so every `BaseModelViewSet`) are compared, and only through their
    `permission_classes` - not a `get_permissions()` override.
    """

    runs_field_guards = True
    # {field name: reason} - a field another viewset over the same serializer guards, left open here.
    unguarded_fields = {}
    actions_writing_no_guarded_fields = {"destroy": "deleting a row writes none of its fields"}
    field_guard_databases = None  # every configured database
    # serializer class -> {(module, qualname): (viewset, guarded field names)}
    _serializer_guards = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        require_reasons(cls.__name__, "unguarded_fields", cls.unguarded_fields)
        require_reasons(cls.__name__, "actions_writing_no_guarded_fields", cls.actions_writing_no_guarded_fields)
        nested = [guard for entry in getattr(cls, "permission_classes", []) for guard in _nested_guards(entry)]
        if nested:
            raise ImproperlyConfigured(
                _(
                    "%(viewset)s composes %(guard)s with &, | or ~ - a guard must be its own "
                    "permission_classes entry, with any composition inside guarding()."
                )
                % {"viewset": cls.__name__, "guard": nested[0].__name__}
            )
        cls._check_shared_serializers()

    @classmethod
    def _declared_guarded_names(cls):
        return frozenset(
            name
            for entry in getattr(cls, "permission_classes", [])
            if isinstance(entry, type) and issubclass(entry, Guard) and entry.fields is not None
            for name in entry.fields
        )

    @classmethod
    def _check_shared_serializers(cls):
        key = (cls.__module__, cls.__qualname__)
        guarded = cls._declared_guarded_names()
        for serializer_cls in cls.declared_serializer_classes():
            users = cls._serializer_guards.setdefault(serializer_cls, {})
            for other_key, (other, other_guarded) in users.items():
                if other_key == key:
                    # the same class body redefined (a re-run test, an autoreload) - replaced below
                    continue
                open_here = other_guarded - guarded - frozenset(cls.unguarded_fields)
                open_there = guarded - other_guarded - frozenset(other.unguarded_fields)
                if open_here or open_there:
                    lenient, strict, fields = (cls, other, open_here) if open_here else (other, cls, open_there)
                    raise ImproperlyConfigured(
                        _(
                            "%(strict)s guards %(fields)s on %(serializer)s, but %(lenient)s uses the same "
                            "serializer without guarding them - guard them there too, or name them in "
                            "%(lenient)s.unguarded_fields with the reason they're open there."
                        )
                        % {
                            "strict": strict.__name__,
                            "fields": sorted(fields),
                            "serializer": serializer_cls.__name__,
                            "lenient": lenient.__name__,
                        }
                    )
            users[key] = (cls, guarded)

    def dispatch(self, request, *args, **kwargs):
        if request.method in SAFE_METHODS or not self._declared_guarded_names():
            return super().dispatch(request, *args, **kwargs)
        self._field_guards_checked = False
        token = _active_guarded_view.set(self)
        aliases = self.field_guard_databases if self.field_guard_databases is not None else list(connections)
        try:
            with ExitStack() as stack:
                for alias in aliases:
                    stack.enter_context(transaction.atomic(using=alias))
                response = super().dispatch(request, *args, **kwargs)
                # a plain Django HttpResponse has no `exception` - it didn't come from DRF's handler
                if getattr(response, "exception", False):
                    # DRF turned an exception - a refused guard, a failed validation - into this response,
                    # so no exception reaches the transaction; roll back whatever the handler wrote first.
                    # An error response the handler returned on purpose keeps its writes, as it does
                    # under ATOMIC_REQUESTS.
                    for alias in aliases:
                        transaction.set_rollback(True, using=alias)
                self._require_field_guards_ran(response)
        finally:
            _active_guarded_view.reset(token)
        return response

    def guards_serializer(self, serializer):
        """
        Whether a serializer saved during this viewset's write request answers to its field guards:
        one for this viewset's model (a proxy or the concrete model alike). Another model's serializer
        saved along the way - an audit log, say - isn't checked against rows it doesn't write.
        """
        serializer_model = getattr(getattr(serializer, "Meta", None), "model", None)
        if serializer_model is None:
            return False
        view_model = getattr(self, "model", None) or self.get_queryset().model
        return serializer_model._meta.concrete_model is view_model._meta.concrete_model

    def _require_field_guards_ran(self, response):
        if response.status_code >= 400 or self._field_guards_checked:
            return
        if self.action in self.actions_writing_no_guarded_fields:
            return
        # A request only succeeds with its action set, so the `or ""`/default fallbacks just keep a hand-rolled
        # dispatch from a TypeError - no test can reach them.
        handler = getattr(self, self.action or "", None)
        if getattr(handler, "writes_no_guarded_fields", False):
            return
        if not self.get_field_guards():  # a get_permissions() override dropped them for this action
            return
        raise ImproperlyConfigured(
            _(
                "%(viewset)s.%(action)s succeeded without running its field guards, so its database writes are "
                "rolled back. Save through a serializer with FieldGuardsOnSaveMixin (BaseModelSerializer), call "
                'self.check_guarded_fields(serializer), or mark the action @writes_no_guarded_fields("<why>") if '
                "it writes no guarded field."
            )
            % {"viewset": type(self).__name__, "action": self.action}
        )

    def get_field_guards(self):
        return [
            permission
            for permission in self.get_permissions()
            if isinstance(permission, Guard) and permission.fields is not None
        ]

    def check_guarded_fields(self, serializer, save_kwargs=None):
        """
        Refuses, with a field-keyed `ValidationError`, a validated write that changes a field one of
        this viewset's field guards refuses. `save_kwargs` are values about to be passed to
        `serializer.save()`, which DRF merges over `validated_data` - they're part of the write too.
        A `many=True` serializer is checked item by item, each as a new row.
        """
        self._field_guards_checked = True
        instance = serializer.instance
        if isinstance(serializer, ListSerializer):
            instance = None
            writes = [(serializer.child.fields, {**item, **(save_kwargs or {})}) for item in serializer.validated_data]
        else:
            writes = [(serializer.fields, {**serializer.validated_data, **(save_kwargs or {})})]
        errors = {}
        for guard in self.get_field_guards():
            touched = []
            for name, target in guard.fields.items():
                if any(self._touches(guard, name, target, fields, data, instance) for fields, data in writes):
                    touched.append(name)
            if touched and not guard.allows(self.request, self, instance):
                for name in touched:
                    errors.setdefault(name, []).append(guard.message)
        if errors:
            raise ValidationError(errors, code="permission_denied")

    def _touches(self, guard, name, target, fields, data, instance):
        if name not in fields:
            # Not writable through this serializer - `?only=`/`?exclude=` dropped it, or this action's
            # serializer doesn't carry it. Only a name no declared serializer has is a mistake.
            self._check_guarded_name_exists(guard, name)
            return False
        field = fields[name]
        if field.read_only:
            return False
        incoming = _dig(data, field.source_attrs)
        if incoming is _MISSING or not target.matches(incoming):
            return False
        return instance is None or _changed(_dig(instance, field.source_attrs), incoming)

    @classmethod
    def declared_serializer_classes(cls):
        """Every serializer class this viewset declares - `serializer_class` and, with
        ActionSerializerClassMixin, each of `serializer_class_action_map`."""
        declared = [getattr(cls, "serializer_class", None), *getattr(cls, "serializer_class_action_map", {}).values()]
        # an abstract base has none yet, or still holds RequiredAttributesMixin's placeholder
        return list(dict.fromkeys(serializer_cls for serializer_cls in declared if isinstance(serializer_cls, type)))

    def _check_guarded_name_exists(self, guard, name):
        # Built without a request, so nothing conditional narrows the fields.
        if not any(name in serializer_cls().fields for serializer_cls in self.declared_serializer_classes()):
            raise ImproperlyConfigured(
                _("%(guard)s guards %(field)r, which no serializer of %(viewset)s has a field named.")
                % {"guard": type(guard).__name__, "field": name, "viewset": type(self).__name__}
            )

    def perform_create(self, serializer):
        if not isinstance(serializer, FieldGuardsOnSaveMixin):  # otherwise its save() checks
            self.check_guarded_fields(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        if not isinstance(serializer, FieldGuardsOnSaveMixin):
            self.check_guarded_fields(serializer)
        super().perform_update(serializer)
