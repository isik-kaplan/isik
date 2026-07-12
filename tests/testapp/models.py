"""
Concrete models used only to exercise isik's Django helpers against a real
database - a real BaseModel subclass, a real many-to-many through a
non-auto_created table, and a real AutoGenericForeignKey.
"""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django_lifecycle import AFTER_CREATE, AFTER_SAVE, AFTER_UPDATE, BEFORE_CREATE, BEFORE_SAVE, BEFORE_UPDATE, hook

from isik.django.apps.common.db import track_events
from isik.django.apps.common.fields.gfk import AutoGenericForeignKey
from isik.django.apps.common.models import BaseModel


def positive_only(value):
    if value < 0:
        raise ValidationError("Must be positive.")


class EmailUser(AbstractUser):
    """A real AUTH_USER_MODEL with USERNAME_FIELD != "username", for UsernameOREmailModelBackend tests."""

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    email = models.EmailField(unique=True)

    class Meta:
        app_label = "testapp"


@track_events()
class Widget(BaseModel):
    name = models.CharField(max_length=100)
    count = models.IntegerField(default=0, validators=[positive_only])
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        app_label = "testapp"


class Tag(BaseModel):
    label = models.CharField(max_length=100)

    class Meta:
        app_label = "testapp"


class WidgetTag(models.Model):
    """Explicit through model - not auto_created, unlike a plain ManyToManyField's default."""

    widget = models.ForeignKey(Widget, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
    note = models.CharField(max_length=100, blank=True)

    class Meta:
        app_label = "testapp"


class TaggedWidget(BaseModel):
    name = models.CharField(max_length=100)
    tags = models.ManyToManyField(Tag, through=WidgetTag, related_name="tagged_widgets")

    class Meta:
        app_label = "testapp"


class Note(BaseModel):
    body = models.CharField(max_length=200)
    target = AutoGenericForeignKey(limit_models_to=[Widget])

    class Meta:
        app_label = "testapp"


class Recorder(BaseModel):
    """Records the order lifecycle hooks fire in, to pin down BaseModel.save()'s contract."""

    STR = "Recorder<{self.name}>"

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "testapp"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hook_log = []

    @hook(BEFORE_CREATE)
    def _before_create(self):
        self.hook_log.append("before_create")

    @hook(AFTER_CREATE)
    def _after_create(self):
        self.hook_log.append("after_create")

    @hook(BEFORE_UPDATE)
    def _before_update(self):
        self.hook_log.append("before_update")

    @hook(AFTER_UPDATE)
    def _after_update(self):
        self.hook_log.append("after_update")

    @hook(BEFORE_SAVE)
    def _before_save(self):
        self.hook_log.append("before_save")

    @hook(AFTER_SAVE)
    def _after_save(self):
        self.hook_log.append("after_save")
