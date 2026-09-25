from contextvars import ContextVar


# The GuardedFieldsMixin viewset handling the current write request, if any - set for the length of
# its dispatch, so a serializer can find the guards it has to answer to however it was built.
_active_guarded_view = ContextVar("isik_active_guarded_view", default=None)


class FieldGuardsOnSaveMixin:
    """
    Runs the current viewset's field guards (`guarding(..., fields=/setting=...)`) inside `save()`,
    before anything reaches the database - so a write is checked however its serializer was built:
    `get_serializer()`, or `SomeSerializer(instance, data=...)` constructed by hand in an overridden
    handler or a custom `@action`. Values passed to `save(**kwargs)` count as part of the write.

    Only a serializer for that viewset's model is checked - another model's serializer saved along the
    way (an audit log) isn't held to guards written for rows it doesn't touch. Does nothing outside a
    GuardedFieldsMixin viewset's write request - a shell, a task, a test saving a serializer directly.
    Part of `BaseModelSerializer`.
    """

    def save(self, **kwargs):
        view = _active_guarded_view.get()
        if view is not None and view.guards_serializer(self):
            view.check_guarded_fields(self, save_kwargs=kwargs)
        return super().save(**kwargs)
