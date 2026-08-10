from django.db.models.deletion import ProtectedError
from rest_framework import status
from rest_framework.response import Response


class ProtectedDestroyMixin:
    """
    Turns a ProtectedError on delete into a 400 response instead of a raw server error, reporting
    which models and which field blocked the delete - not the individual blocking objects, since
    there can be a lot of them and the caller can query for them directly if they need the list:

        {"protected_by": [{"model": "Comment", "field": "widget"}]}
    """

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError as error:
            instance = self.get_object()
            blockers = {
                (type(protected_object).__name__, self._protecting_field_name(protected_object, instance))
                for protected_object in error.protected_objects
            }
            data = [{"model": model, "field": field} for model, field in sorted(blockers)]
            return Response({"protected_by": data}, status=status.HTTP_400_BAD_REQUEST)

    @staticmethod
    def _protecting_field_name(protected_object, instance):
        for field in type(protected_object)._meta.get_fields():
            if not getattr(field, "is_relation", False) or not getattr(field, "concrete", False):  # pragma: no mutate
                # is_relation/concrete are standard attributes Django sets on every Field/relation
                # descriptor from _meta.get_fields() (defaulting to False on the base Field class
                # itself) - never actually missing, so these getattr() defaults are unreachable.
                continue
            if field.related_model is not type(instance):
                continue
            fk_value = getattr(protected_object, field.attname, None)  # pragma: no mutate
            # field is concrete+relational (checked above), so attname is a real descriptor Django
            # put on this exact class via contribute_to_class() - always present on any instance
            # of it, even lazily-loaded/deferred ones, so this default can never be consulted.
            if fk_value == instance.pk:
                return field.name
        return None
