"""
Who a kind is for. An audience is anything with `recipients(notice)` - the users it names, read inside the
partition the notice is being delivered in - so a project's own (a subject's owners, an installation's
holders, the platform's operators) is a small class:

    class Operators(Audience):
        def recipients(self, notice):
            return get_user_model().objects.filter(is_staff=True)

`partitions(notice)` names the partitions it reaches - the notice's own by default - and
`spans_partitions` says whether that can be more than one, which a single-partition kind's audience
mustn't. Two ship: `Field`, the users the subject row names, and `Subscribers`, those who asked.
"""

from django.apps import apps
from django.contrib.auth import get_user_model

from isik._internal.translation import gettext as _


SNAPSHOT = "snapshot"
LIVE = "live"


class Audience:
    """The base of an audience - override `recipients()`, and `partitions()` for one spanning partitions."""

    spans_partitions = False

    def recipients(self, notice):
        """The users `notice` is for, in the partition it's being delivered in - a queryset or an iterable."""
        raise NotImplementedError

    def partitions(self, notice):
        """The partitions `notice` reaches - its subject's own, by default."""
        return [notice.subject_partition]

    def snapshot_fields(self):
        """The subject's fields this audience reads from a notice's snapshot - each one the kind must carry."""
        return ()

    def __str__(self):
        return type(self).__name__


class Field(Audience):
    """
    The users the subject row names, by its fields keying to the user model: `Field("from_user",
    "to_user")`. Read from the notice's snapshot - who they were when it happened - so the kind's
    `snapshot` has to carry each field, which a check holds it to. `at=LIVE` reads the subject row as
    it is when the notice is delivered instead, for a kind meaning whoever holds it now.
    """

    def __init__(self, *fields, at=SNAPSHOT):
        if not fields:
            raise TypeError(_("Field() names at least one field."))
        if at not in (SNAPSHOT, LIVE):
            raise ValueError(_("Field()'s at= is SNAPSHOT or LIVE, not %(at)r.") % {"at": at})
        self.fields, self.at = fields, at

    def snapshot_fields(self):
        return self.fields if self.at == SNAPSHOT else ()

    def _keys(self, notice):
        if self.at == SNAPSHOT:
            return [notice.snapshot.get(name) for name in self.fields]
        subject = apps.get_model(notice.subject_label)._default_manager.filter(pk=notice.subject_id).first()
        if subject is None:
            return []
        return [getattr(subject, subject._meta.get_field(name).attname) for name in self.fields]

    def recipients(self, notice):
        keys = {key for key in self._keys(notice) if key is not None}
        return get_user_model()._default_manager.filter(pk__in=keys)

    def __str__(self):
        names = ", ".join(repr(name) for name in self.fields)
        return f"Field({names})" if self.at == SNAPSHOT else f"Field({names}, at=LIVE)"


class Subscribers(Audience):
    """Everyone holding an enabled subscription covering the notice's kind and variant."""

    def recipients(self, notice):
        from isik.django.apps.notifications.audience import get_subscription_model
        from isik.django.apps.notifications.kinds import matching_variant

        subscriptions = matching_variant(
            get_subscription_model()._default_manager.filter(kind_id=notice.kind_id, enabled=True), notice.variant
        )
        return get_user_model()._default_manager.filter(pk__in=subscriptions.values("user"))
