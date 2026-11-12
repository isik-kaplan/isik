"""The features models isik ships - each swapped out by its `ISIK_FEATURES_*_MODEL` setting. The overrides model is
always the project's own (`AbstractOverrides`), and so is the grant model wherever the holder isn't the user: a
migration of isik's can only name a holder fixed when it was written."""

from django.conf import settings
from django.db import models

from isik._internal.translation import gettext_lazy
from isik.django.apps.common.db.history import history_swapped_with, track_events
from isik.django.apps.features.bases import ERASE, AbstractFeature, AbstractFeatureGrant, AbstractSweepCursor
from isik.django.apps.features.swapping import FEATURE_MODEL, GRANT_HISTORY_MODEL, GRANT_MODEL, SWEEP_MODEL


class Feature(AbstractFeature):
    class Meta(AbstractFeature.Meta):
        db_table = "isik_feature"
        swappable = FEATURE_MODEL
        verbose_name = gettext_lazy("feature")
        verbose_name_plural = gettext_lazy("features")


# Its history says when each grant was revoked - swapped out with it.
@track_events(meta={"swappable": GRANT_HISTORY_MODEL})
class FeatureGrant(AbstractFeatureGrant):
    holder = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=ERASE,
        related_name="+",
        help_text=gettext_lazy("Who the feature is granted to."),
        verbose_name=gettext_lazy("holder"),
        db_comment="The holder the feature is granted to. Erased with the holder.",
    )

    class Meta(AbstractFeatureGrant.Meta):
        db_table = "isik_feature_grant"
        swappable = GRANT_MODEL
        verbose_name = gettext_lazy("feature grant")
        verbose_name_plural = gettext_lazy("feature grants")


history_swapped_with(FeatureGrant, GRANT_MODEL)


class SweepCursor(AbstractSweepCursor):
    class Meta(AbstractSweepCursor.Meta):
        db_table = "isik_sweep_cursor"
        swappable = SWEEP_MODEL
        verbose_name = gettext_lazy("sweep cursor")
        verbose_name_plural = gettext_lazy("sweep cursors")
