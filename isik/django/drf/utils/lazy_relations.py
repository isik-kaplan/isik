from rest_framework import serializers
from rest_framework.serializers import PrimaryKeyRelatedField


class LazyPrimaryKeyRelatedField(PrimaryKeyRelatedField):
    """
    A PrimaryKeyRelatedField whose queryset is resolved lazily via `queryset_func`,
    called on every access instead of once at class-body/import time. Lets a serializer
    reference a model that would otherwise need an eager import, avoiding circular
    imports between serializer modules.

        reviewer = LazyPrimaryKeyRelatedField(queryset_func=lambda: User.objects.all())
    """

    def __init__(self, *args, **kwargs):
        self.queryset_func = kwargs.pop("queryset_func")
        super().__init__(*args, **kwargs)  # pragma: no mutate
        # PrimaryKeyRelatedField.__init__ takes **kwargs only - *args is always empty here, so
        # dropping it from the passthrough is unobservable.

    def get_queryset(self):
        return self.queryset_func()


class LazyGenericRelatedField(serializers.Field):
    """
    Serializes a GenericForeignKey by delegating to whichever registered serializer
    matches the target model. `serializers_func` is called on every access and must
    return a `{Model: serializer_instance}` dict - built lazily so it can reference
    sibling serializer modules without an eager, potentially circular import.

    Read: picks the first entry in `type(instance).mro()` found in the dict, so a
    registered superclass is used as a fallback for unregistered subclasses.

    Write: since raw input carries no type tag, every registered serializer's
    `to_internal_value` is tried; exactly one must succeed or the field fails with
    `no_data_match` (0 matches) or `ambiguous_data` (more than 1 - the input shape
    isn't unique to any single registered model).

        resource = LazyGenericRelatedField(lambda: {Note: NoteSerializer(), Widget: WidgetSerializer()})
    """

    default_error_messages = {
        "no_model_match": "Invalid model - no serializer registered for it.",
        "no_data_match": "Could not determine a serializer for the given data.",
        "ambiguous_data": "Could not determine a unique serializer for the given data.",
    }

    def __init__(self, serializers_func, *args, **kwargs):
        self.serializers_func = serializers_func
        super().__init__(*args, **kwargs)  # pragma: no mutate
        # serializers.Field.__init__ is keyword-only (no positional params at all) - *args is
        # always empty here, so dropping it from the passthrough is unobservable.

    @property
    def serializers(self):
        serializers_by_model = self.serializers_func()
        for serializer in serializers_by_model.values():
            serializer.bind("", self)
        return serializers_by_model

    def to_representation(self, instance):
        serializers_by_model = self.serializers
        for klass in type(instance).mro():
            if klass in serializers_by_model:
                return serializers_by_model[klass].to_representation(instance)
        self.fail("no_model_match")

    def to_internal_value(self, data):
        matches = []
        for serializer in self.serializers.values():
            try:
                matches.append(serializer.to_internal_value(data))
            except Exception:
                continue
        if not matches:
            self.fail("no_data_match")
        if len(matches) > 1:
            self.fail("ambiguous_data")
        return matches[0]
