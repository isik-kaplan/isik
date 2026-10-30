"""Mirroring the declared features into the catalog after `migrate` - adding rows, never removing one."""

from isik.django.apps.features import declarations
from isik.django.apps.features.swapping import get_feature_model


def mirror_features(**kwargs):
    """Writes a catalog row for each declared feature that has none - a removed declaration keeps its row."""
    model = get_feature_model()
    known = set(model._default_manager.values_list("name", flat=True))
    model._default_manager.bulk_create([model(name=name) for name in declarations.features if name not in known])
