from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import ValidationError

from isik._internal.translation import gettext as _
from isik.django.drf._changes import MISSING, changed, dig


CREATE_ONLY_CHANGES = ("ignore", "refuse")


class CreateOnlyFieldsMixin:
    """
    Fields listed in `Meta.create_only_fields` are settable at creation, then locked: not required on
    any update after that, and never changed by one.

        class Meta:
            model = Widget
            fields = ["id", "slug", "name"]
            create_only_fields = ["slug"]
            create_only_changes = "refuse"

    `Meta.create_only_changes` says what an update trying to change one gets:

    - `"ignore"` (the default) - the field is read-only on update, so a value sent for it is dropped
      and the update answers as if it hadn't been. Right for a field a client echoes back unchanged.
    - `"refuse"` - a value that differs from the stored one is a 400 naming the field, which a
      caller trying to move it can act on. The same value sent back unchanged still passes, so one
      serializer serves a client that echoes the field and refuses one that tries to change it.
    """

    def _create_only_changes(self):
        changes = getattr(self.Meta, "create_only_changes", "ignore")
        if changes not in CREATE_ONLY_CHANGES:
            raise ImproperlyConfigured(
                _("%(serializer)s.Meta.create_only_changes is %(value)r - it takes 'ignore' or 'refuse'.")
                % {"serializer": type(self).__name__, "value": changes}
            )
        return changes

    def get_extra_kwargs(self):
        kwargs = super().get_extra_kwargs()
        # get_extra_kwargs() is a ModelSerializer concept - super().get_extra_kwargs() above
        # already requires a Meta, so no need to guard against a missing one here too.
        create_only_fields = getattr(self.Meta, "create_only_fields", None)
        if self.instance and create_only_fields:
            # Refusing has to see the value, so the field stays writable - only no longer required.
            locked = {"read_only": True} if self._create_only_changes() == "ignore" else {"required": False}
            for field in create_only_fields:
                kwargs.setdefault(field, {})
                kwargs[field].update(locked)
        return kwargs

    def to_internal_value(self, data):
        validated = super().to_internal_value(data)
        create_only_fields = getattr(self.Meta, "create_only_fields", None)
        # Ignoring makes the fields read-only on update, so none of them reaches `validated` to compare.
        if not (self.instance and create_only_fields):
            return validated
        refused = {}
        for name in create_only_fields:
            source_attrs = self.fields[name].source_attrs
            incoming = dig(validated, source_attrs)
            if incoming is not MISSING and changed(dig(self.instance, source_attrs), incoming):
                refused[name] = [_("This field can't be changed once it's set.")]
        if refused:
            raise ValidationError(refused, code="create_only")
        return validated
