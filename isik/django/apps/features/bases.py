"""
The features app's abstract bases. Each concrete subclass gets its rules when it's prepared, whatever its own Meta
inherits:

- `AbstractFeature` - the catalog of declared features: a row outlives a removed declaration, so its grants keep
  naming something. Refuses a delete and a TRUNCATE.
- `AbstractFeatureGrant` - a feature granted to a holder from a source, between two times. Append-only: never
  deleted or truncated, and an update may only revoke it, once - a grant after a revoke is a new row. When it was
  revoked is its history's to say, which a check requires it to have.
- `AbstractOverrides` - the project's own row per holder: what each setting is forced to (and by whom, and why),
  and the holder's own value where it lives here. Its columns come from the declared settings; history-tracking
  it is the project's, and a check holds it to that.
- `AbstractSweepCursor` - where the boundary sweep got to.
"""

import pgtrigger
from django.db import models
from django.db.models.functions import Now
from django.db.models.signals import class_prepared
from django.utils import timezone

from isik._internal.translation import gettext_lazy, lazy_format
from isik.django.apps.common.db.constraints import fitted_name
from isik.django.apps.common.db.models import DatabaseTimestampsModel, UUIDPrimaryKeyModel, new_primary_key
from isik.django.apps.common.db.triggers import ProtectTruncate
from isik.django.apps.features import conf
from isik.django.apps.features import declarations as declared
from isik.django.apps.features.swapping import FEATURE_MODEL, HOLDER_MODEL, model_name


def ERASE(collector, field, sub_objs, using):
    """
    A holder key's `on_delete`: deleting the holder erases its rows and their history, as `erase_holder()` does,
    inside the delete's transaction. Its rows refuse a delete, so Django's collector leaves them be and the
    holder's `pre_delete` erases them. A project refusing a holder's delete instead declares the key `PROTECT`.
    """


class AbstractFeature(UUIDPrimaryKeyModel):
    """A declared feature, kept as a row: what a grant names, outliving its declaration."""

    name = models.CharField(
        max_length=100,
        unique=True,
        help_text=gettext_lazy("The feature's name, as its declaration gives it."),
        db_comment="The feature's name, as its declaration gives it. Never changes.",
        verbose_name=gettext_lazy("name"),
    )
    created_at = models.DateTimeField(
        db_default=Now(),
        editable=False,
        help_text=gettext_lazy("When it was first declared."),
        db_comment="When the feature was first mirrored from its declaration.",
        verbose_name=gettext_lazy("created at"),
    )

    class Meta:
        abstract = True

    def __str__(self):
        return self.name


class GrantStatus(models.TextChoices):
    """Whether a grant stands - apart from its term: an active grant may not be in force yet, or any longer."""

    ACTIVE = "active", gettext_lazy("active")
    REVOKED = "revoked", gettext_lazy("revoked")


class AbstractFeatureGrant(models.Model):
    """A feature granted to a holder, from a source - in force from `starts_at` until it expires or is revoked."""

    id = models.UUIDField(
        primary_key=True,
        editable=False,
        default=new_primary_key,
        help_text=gettext_lazy("The grant's own identifier."),
        db_comment="The grant's own identifier.",
        verbose_name=gettext_lazy("ID"),
    )
    holder = models.ForeignKey(
        model_name(HOLDER_MODEL),
        on_delete=ERASE,
        related_name="+",
        help_text=gettext_lazy("Who the feature is granted to."),
        db_comment="The holder the feature is granted to. Erased with the holder.",
        verbose_name=gettext_lazy("holder"),
    )
    feature = models.ForeignKey(
        model_name(FEATURE_MODEL),
        on_delete=models.PROTECT,
        related_name="+",
        help_text=gettext_lazy("The feature granted."),
        db_comment="The feature granted.",
        verbose_name=gettext_lazy("feature"),
    )
    source = models.CharField(
        max_length=64,
        help_text=gettext_lazy("Where the grant came from - a subscription, a trial, by hand."),
        db_comment="One of the project's GRANT_SOURCES.",
        verbose_name=gettext_lazy("source"),
    )
    reference = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=gettext_lazy("What the source knows the grant by - an invoice, say."),
        db_comment="The source's own reference for the grant, or empty.",
        verbose_name=gettext_lazy("reference"),
    )
    starts_at = models.DateTimeField(
        default=timezone.now,
        help_text=gettext_lazy("When it comes into force."),
        db_comment="When the grant comes into force.",
        verbose_name=gettext_lazy("starts at"),
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When it ends, or none for never."),
        db_comment="When the grant ends, or null for never.",
        verbose_name=gettext_lazy("expires at"),
    )
    status = models.CharField(
        max_length=16,
        choices=GrantStatus.choices,
        default=GrantStatus.ACTIVE,
        db_default=GrantStatus.ACTIVE,
        help_text=gettext_lazy("Whether it stands, or was revoked."),
        db_comment="active, or revoked - once, never undone. When it was revoked is in the grant's history.",
        verbose_name=gettext_lazy("status"),
    )
    created_at = models.DateTimeField(
        db_default=Now(),
        editable=False,
        help_text=gettext_lazy("When it was granted."),
        db_comment="When the grant was written.",
        verbose_name=gettext_lazy("created at"),
    )

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.feature_id} for {self.holder_id}"


class AbstractOverrides(UUIDPrimaryKeyModel, DatabaseTimestampsModel):
    """
    The project's row of a holder's settings: per declared setting, whether it's forced, to what, by which source
    and why - and the holder's own value, where it lives here. Created on its first write; until then each
    setting is its default. `updated_at` is its version, so one ETag covers all of a holder's settings.
    """

    holder = models.OneToOneField(
        model_name(HOLDER_MODEL),
        on_delete=ERASE,
        related_name="+",
        help_text=gettext_lazy("Whose settings these are."),
        db_comment="The holder these settings are of. Erased with the holder.",
        verbose_name=gettext_lazy("holder"),
    )

    class Meta:
        abstract = True

    def __str__(self):
        return f"settings of {self.holder_id}"


class AbstractSweepCursor(UUIDPrimaryKeyModel):
    """Where the boundary sweep got to: grants starting or ending since `swept_at` are told when it next runs."""

    swept_at = models.DateTimeField(
        help_text=gettext_lazy("When the boundary sweep last ran."),
        db_comment="When the boundary sweep last ran - the next one looks at the boundaries since.",
        verbose_name=gettext_lazy("swept at"),
    )
    next_sweep_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When the next boundary sweep is scheduled for."),
        db_comment=(
            "When the next boundary sweep is scheduled for, with ISIK['DJANGO']['FEATURES']['SWEEP'] next_boundary - "
            "none is scheduled beside one due no later. Null with interval, and before the first."
        ),
        verbose_name=gettext_lazy("next sweep at"),
    )

    class Meta:
        abstract = True


def _register(model, *triggers):
    for trigger in triggers:
        pgtrigger.register(trigger)(model)


def _rules(sender, **kwargs):
    if sender._meta.abstract or sender._meta.proxy:
        return
    if issubclass(sender, AbstractFeature):
        _register(
            sender,
            pgtrigger.Protect(name="protect_feature_delete", operation=pgtrigger.Delete),
            ProtectTruncate(name="protect_feature_truncate"),
        )
    if issubclass(sender, AbstractFeatureGrant):
        _register(
            sender,
            pgtrigger.Protect(name="protect_grant_delete", operation=pgtrigger.Delete),
            ProtectTruncate(name="protect_grant_truncate"),
            # Only its status may change, and only to revoked: a revoke is written once and never undone. Nor is a grant
            # written revoked - its history would have no revoke to say when.
            pgtrigger.Protect(
                name="protect_grant_revoked",
                operation=pgtrigger.Update,
                condition=pgtrigger.Q(old__status=GrantStatus.REVOKED),
            ),
            pgtrigger.Protect(
                name="protect_grant_written_revoked",
                operation=pgtrigger.Insert,
                condition=pgtrigger.Q(new__status=GrantStatus.REVOKED),
            ),
            pgtrigger.Protect(
                name="protect_grant_terms",
                operation=pgtrigger.Update,
                condition=pgtrigger.AnyChange(exclude=["status"]),
            ),
        )
    if issubclass(sender, AbstractOverrides):
        _overrides_columns(sender)


def _overrides_columns(model):
    """Each declared setting's columns on the project's overrides model, and the constraints tying them."""
    conf.import_settings_module()
    table = model._meta.db_table
    constraints = []
    for setting in declared.settings.values():
        name = setting.name
        if isinstance(setting.own, declared.OnOverrides):
            model.add_to_class(name, _nullable(setting.field, gettext_lazy("The holder's own choice, or none.")))
        model.add_to_class(
            f"{name}_forced",
            models.BooleanField(
                default=False,
                db_default=False,
                help_text=gettext_lazy("Whether it's forced, so the holder can't change it."),
                db_comment=f"Whether {name} is forced - {name}_forced_value then decides.",
            ),
        )
        model.add_to_class(
            f"{name}_forced_value", _nullable(setting.field, gettext_lazy("What it's forced to, while forced."))
        )
        model.add_to_class(
            f"{name}_forced_source",
            models.CharField(
                max_length=100,
                null=True,
                blank=True,
                help_text=gettext_lazy("The force source that forced it, while forced."),
                db_comment=f"The declared force source that forced {name}, else null.",
            ),
        )
        model.add_to_class(
            f"{name}_forced_reason",
            models.TextField(
                null=True,
                blank=True,
                help_text=gettext_lazy("Why it was forced, while forced."),
                db_comment=f"Why {name} was forced, else null.",
            ),
        )
        unforced = models.Q(
            **{
                f"{name}_forced": False,
                f"{name}_forced_value__isnull": True,
                f"{name}_forced_source__isnull": True,
                f"{name}_forced_reason__isnull": True,
            }
        )
        forced = models.Q(
            **{f"{name}_forced": True, f"{name}_forced_source__isnull": False, f"{name}_forced_reason__isnull": False}
        )
        if setting.field.null:
            message = lazy_format(
                "A forced %(setting)s needs its source and its reason, and an unforced one has no forced value, "
                "source or reason.",
                setting=name,
            )
        else:
            # Forcing a setting that can't be null to null isn't a value - it's a force of nothing.
            forced &= models.Q(**{f"{name}_forced_value__isnull": False})
            message = lazy_format(
                "A forced %(setting)s needs a value, its source and its reason, and an unforced one has none of them.",
                setting=name,
            )
        constraints.append(
            models.CheckConstraint(
                condition=unforced | forced,
                name=fitted_name(table, f"{name}_forced_exactly"),
                violation_error_message=message,
                violation_error_code="forced_exactly",
            )
        )
    # A new list: one inherited from a parent Meta is that Meta's own; and original_attrs is what a migration's
    # state is built from.
    model._meta.constraints = [*model._meta.constraints, *constraints]
    model._meta.original_attrs["constraints"] = model._meta.constraints


def _nullable(field, help_text):
    column = field.clone()
    column.null, column.blank, column.default, column.db_default = True, True, None, models.NOT_PROVIDED
    column.help_text, column.db_comment = help_text, str(help_text)
    return column


class_prepared.connect(_rules, dispatch_uid="isik_features_rules")
