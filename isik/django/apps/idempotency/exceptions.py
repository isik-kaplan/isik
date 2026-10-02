"""What a request carrying an idempotency key is refused with - each an `APIException`, so DRF renders it."""

from rest_framework.exceptions import APIException

from isik._internal.translation import gettext_lazy


class IdempotencyKeyReused(APIException):
    """The key was spent on a request with a different method, path, query or body."""

    status_code = 422
    default_detail = gettext_lazy("This idempotency key was already used for a different request.")
    default_code = "idempotency_key_reused"


class IdempotencyKeyNotReplayable(APIException):
    """The key was spent on an action declared in `idempotency_no_replay_actions`."""

    status_code = 409
    default_detail = gettext_lazy("This idempotency key was already used, and its response can't be sent again.")
    default_code = "idempotency_key_not_replayable"


class IdempotencyKeyInFlight(APIException):
    """`IDEMPOTENCY_LOCK_TIMEOUT` ran out while the first request with this key was still being served."""

    status_code = 409
    default_detail = gettext_lazy("A request with this idempotency key is still being served. Retry it shortly.")
    default_code = "idempotency_key_in_flight"


class IdempotentReplayGone(APIException):
    """The request this key was spent on succeeded, but the row it returned no longer exists."""

    status_code = 410
    default_detail = gettext_lazy("The request with this idempotency key succeeded, but what it returned is gone.")
    default_code = "idempotent_replay_gone"
