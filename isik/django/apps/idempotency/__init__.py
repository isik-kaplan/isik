"""
Idempotency keys for DRF views - see `drf.IdempotencyMixin`.

This package holds the abstract claims and the logic, and has no tables of its own. Install exactly
one of the two apps beside it, which each own one concrete claim and its migration:

- `isik.django.apps.idempotency.by_reference` - `IdempotencyClaim`, which replays a response by
  re-serializing the row it named.
- `isik.django.apps.idempotency.with_body` - `IdempotencyClaimWithBody`, which keeps the response
  body itself.

Or subclass one of the abstract claims in `claims` in an app of your own, and point
`IDEMPOTENCY_CLAIM_MODEL` at it.
"""
