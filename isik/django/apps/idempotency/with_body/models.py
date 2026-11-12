from isik._internal.translation import gettext_lazy
from isik.django.apps.idempotency.claims import AbstractIdempotencyClaimWithBody


class IdempotencyClaimWithBody(AbstractIdempotencyClaimWithBody):
    """A claim replayed from the body it kept - see `AbstractIdempotencyClaimWithBody`."""

    class Meta(AbstractIdempotencyClaimWithBody.Meta):
        db_table = "isik_idempotency_claim_with_body"
        verbose_name = gettext_lazy("idempotency claim with body")
        verbose_name_plural = gettext_lazy("idempotency claims with body")
