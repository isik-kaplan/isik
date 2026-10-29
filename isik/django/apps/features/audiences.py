"""
`HolderPeople` - the audience of a notice about a holder: who a grant, a revoke, a force or a release reaches.
Declare a kind on the grant or overrides model with it, its snapshot carrying the holder:

    @notifies(FEATURE_GRANTED, on=pgtrigger.Insert, snapshot=["holder", "feature"], audience=HolderPeople(), ...)
    class FeatureGrant(AbstractFeatureGrant): ...

The people are `PEOPLE(holder)` - the holder itself, when it's a user, unless the hook says otherwise (an
organization's owners, say).
"""

from django.contrib.auth import get_user_model

from isik.django.apps.features import conf
from isik.django.apps.features.swapping import get_holder_model


class HolderPeople:
    """The people of the holder a notice's snapshot names under `field` ("holder")."""

    spans_partitions = False

    def __init__(self, field="holder"):
        self.field = field

    def __str__(self):
        return f"HolderPeople({self.field!r})"

    def snapshot_fields(self):
        return (self.field,)

    def partitions(self, notice):
        return [notice.subject_partition]

    def recipients(self, notice):
        holder = get_holder_model()._default_manager.filter(pk=notice.snapshot.get(self.field)).first()
        if holder is None:
            return []
        hook = conf.setting("PEOPLE")
        if hook is not None:
            return list(hook(holder))
        return [holder] if isinstance(holder, get_user_model()) else []
