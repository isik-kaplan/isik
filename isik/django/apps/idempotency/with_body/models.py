from isik.django.apps.idempotency.claims import AbstractIdempotencyClaimWithBody


class IdempotencyClaimWithBody(AbstractIdempotencyClaimWithBody):
    """A claim replayed from the body it kept - see `AbstractIdempotencyClaimWithBody`."""
