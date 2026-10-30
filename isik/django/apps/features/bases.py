"""
The features app's abstract bases. Each concrete subclass gets its rules when it's prepared, whatever its own Meta
inherits:

- `AbstractFeature` - the catalog of declared features: a row outlives a removed declaration, so its grants keep
  naming something. Refuses a delete and a TRUNCATE.
- `AbstractFeatureGrant` - a feature granted to a holder from a source, between two times. Append-only: never
  deleted or truncated, and an update may only set `revoked_at` once - a grant after a revoke is a new row.
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

from isik._internal.translation import gettext_lazy
from isik.django.apps.common.db.constraints import fitted_name
from isik.django.apps.common.db.models import DatabaseTimestampsModel, new_primary_key
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


class AbstractFeature(models.Model):
    """A declared feature, kept as a row: what a grant names, outliving its declaration."""

    name = models.CharField(
        max_length=100,
        primary_key=True,
        help_text=gettext_lazy("The feature's name, as its declaration gives it."),
        db_comment="The feature's name, as its declaration gives it. Never changes.",
    )
    created_at = models.DateTimeField(
        db_default=Now(),
        editable=False,
        help_text=gettext_lazy("When it was first declared."),
        db_comment="When the feature was first mirrored from its declaration.",
    )

    class Meta:
        abstract = True

    def __str__(self):
        return self.name


class AbstractFeatureGrant(models.Model):
    """A feature granted to a holder, from a source - in force from `starts_at` until it expires or is revoked."""

    id = models.UUIDField(
        primary_key=True,
        editable=False,
        default=new_primary_key,
        help_text=gettext_lazy("The grant's own identifier."),
        db_comment="The grant's own identifier.",
    )
    holder = models.ForeignKey(
        model_name(HOLDER_MODEL),
        on_delete=ERASE,
        related_name="+",
        help_text=gettext_lazy("Who the feature is granted to."),
        db_comment="The holder the feature is granted to. Erased with the holder.",
    )
    feature = models.ForeignKey(
        model_name(FEATURE_MODEL),
        on_delete=models.PROTECT,
        related_name="+",
        help_text=gettext_lazy("The feature granted."),
        db_comment="The feature granted.",
    )
    source = models.CharField(
        max_length=64,
        help_text=gettext_lazy("Where the grant came from - a subscription, a trial, by hand."),
        db_comment="One of the project's GRANT_SOURCES.",
    )
    reference = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=gettext_lazy("What the source knows the grant by - an invoice, say."),
        db_comment="The source's own reference for the grant, or empty.",
    )
    starts_at = models.DateTimeField(
        default=timezone.now,
        help_text=gettext_lazy("When it comes into force."),
        db_comment="When the grant comes into force.",
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When it ends, or none for never."),
        db_comment="When the grant ends, or null for never.",
    )
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When it was revoked, or none."),
        db_comment="When the grant was revoked - written once, or null.",
    )
    created_at = models.DateTimeField(
        db_default=Now(),
        editable=False,
        help_text=gettext_lazy("When it was granted."),
        db_comment="When the grant was written.",
    )

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.feature_id} for {self.holder_id}"


class AbstractOverrides(DatabaseTimestampsModel):
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
    )

    class Meta:
        abstract = True

    def __str__(self):
        return f"settings of {self.holder_id}"


class AbstractSweepCursor(models.Model):
    """Where the boundary sweep got to: grants starting or ending since `swept_at` are told when it next runs."""

    swept_at = models.DateTimeField(
        help_text=gettext_lazy("When the boundary sweep last ran."),
        db_comment="When the boundary sweep last ran - the next one looks at the boundaries since.",
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
            # Only revoked_at may change, and only from null: a revoke is written once, never undone or redated.
            pgtrigger.Protect(
                name="protect_grant_revoked",
                operation=pgtrigger.Update,
                condition=pgtrigger.Q(old__revoked_at__isnull=False),
            ),
            pgtrigger.Protect(
                name="protect_grant_terms",
                operation=pgtrigger.Update,
                condition=pgtrigger.AnyChange(exclude=["revoked_at"]),
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
        if not setting.field.null:
            # Forcing a setting that can't be null to null isn't a value - it's a force of nothing.
            forced &= models.Q(**{f"{name}_forced_value__isnull": False})
        constraints.append(
            models.CheckConstraint(condition=unforced | forced, name=fitted_name(table, f"{name}_forced_exactly"))
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
