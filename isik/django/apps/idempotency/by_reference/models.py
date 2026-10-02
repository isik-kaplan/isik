from isik.django.apps.idempotency.claims import AbstractIdempotencyClaim


class IdempotencyClaim(AbstractIdempotencyClaim):
    """A claim replayed by re-serializing the row its request named - see `AbstractIdempotencyClaim`."""
