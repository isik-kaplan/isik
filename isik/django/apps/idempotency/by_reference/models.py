from isik.django.apps.idempotency.claims import AbstractIdempotencyClaim


class IdempotencyClaim(AbstractIdempotencyClaim):
    """A claim replayed by re-serializing the row its request named - see `AbstractIdempotencyClaim`."""

    class Meta(AbstractIdempotencyClaim.Meta):
        db_table = "isik_idempotency_claim"
