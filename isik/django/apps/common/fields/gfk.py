from functools import reduce

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q


class AutoGenericForeignKey(GenericForeignKey):
    """
    A GenericForeignKey subclass that automatically creates its companion
    content type and object ID fields, reducing three-field boilerplate to one.

    Arguments:
        limit_models_to: Optional list of models to restrict the content type
            choices to. Each entry can be a model class or an "app_label.ModelName"
            string.
        on_delete: Deletion behaviour for the content type ForeignKey.
            Defaults to models.CASCADE.
        object_id_field: Field class to use for the object ID. Defaults to
            models.UUIDField.
        object_id_field_kwargs: Dict of kwargs forwarded to the object ID field
            constructor.

    The companion fields are named <field_name>_content_type and
    <field_name>_object_id, so a field declared as `target = AutoGenericForeignKey()`
    produces `target_content_type` and `target_object_id` on the model.

    Basic usage::

        class MyModel(models.Model):
            target = AutoGenericForeignKey()

    Restrict to specific models::

        class MyModel(models.Model):
            target = AutoGenericForeignKey(
                limit_models_to=[SomeModel, "otherapp.OtherModel"]
            )

    Use a CharField instead of UUIDField for the object ID::

        class MyModel(models.Model):
            target = AutoGenericForeignKey(
                object_id_field=models.CharField,
                object_id_field_kwargs={"max_length": 255},
            )

    Protect against content type deletion::

        class MyModel(models.Model):
            target = AutoGenericForeignKey(on_delete=models.PROTECT)
    """

    def __init__(self, *args, **kwargs):
        self.limit_models_to = kwargs.pop("limit_models_to", None)
        self.on_delete = kwargs.pop("on_delete", models.CASCADE)
        self.object_id_field_class = kwargs.pop("object_id_field", None)
        self.object_id_field_kwargs = kwargs.pop("object_id_field_kwargs", {})
        self.ct_field_name = None
        self.fk_field_name = None
        super().__init__(*args, **kwargs)

    @property
    def limit_gfk_models_to(self):
        if self.limit_models_to:
            q_objects = [
                (
                    Q(app_label=model._meta.app_label, model=model._meta.model_name)
                    if isinstance(model, type)
                    else Q(app_label=model.split(".")[0], model=model.split(".")[1].lower())
                )
                for model in self.limit_models_to
            ]

            return reduce(lambda x, y: x | y, q_objects) if q_objects else None

        return None

    def contribute_to_class(self, cls, name, **kwargs):
        self.ct_field_name = f"{name}_content_type"
        self.fk_field_name = f"{name}_object_id"

        content_type_field = models.ForeignKey(
            ContentType,
            related_name=f"{cls.__name__.lower()}_{name}+",
            on_delete=self.on_delete,
            limit_choices_to=self.limit_gfk_models_to,
        )
        content_type_field.contribute_to_class(cls, self.ct_field_name)

        id_field_type = self.object_id_field_class or models.UUIDField
        id_field_type(**self.object_id_field_kwargs).contribute_to_class(cls, self.fk_field_name)

        self.ct_field = self.ct_field_name
        self.fk_field = self.fk_field_name

        super().contribute_to_class(cls, name)  # pragma: no mutate
