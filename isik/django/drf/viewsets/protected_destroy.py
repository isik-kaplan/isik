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
                continue
            if field.related_model is not type(instance):
                continue
            fk_value = getattr(protected_object, field.attname, None)  # pragma: no mutate
            if fk_value == instance.pk:
                return field.name
        return None
