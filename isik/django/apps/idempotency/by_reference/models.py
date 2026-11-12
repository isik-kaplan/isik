from isik._internal.translation import gettext_lazy
from isik.django.apps.idempotency.claims import AbstractIdempotencyClaim


class IdempotencyClaim(AbstractIdempotencyClaim):
    """A claim replayed by re-serializing the row its request named - see `AbstractIdempotencyClaim`."""

    class Meta(AbstractIdempotencyClaim.Meta):
        db_table = "isik_idempotency_claim"
        verbose_name = gettext_lazy("idempotency claim")
        verbose_name_plural = gettext_lazy("idempotency claims")
