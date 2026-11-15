from django.db import models

from isik._internal.translation import gettext_lazy


class Operation(models.TextChoices):
    """What one sync log entry says happened."""

    UPSERT = "upsert", gettext_lazy("written")
    DELETE = "delete", gettext_lazy("deleted")
    SCOPE_GRANTED = "scope_granted", gettext_lazy("scope granted")
    SCOPE_REVOKED = "scope_revoked", gettext_lazy("scope revoked")
    # Where pruning cut the log: a cursor before it is past retention.
    PRUNED = "pruned", gettext_lazy("pruned up to here")


ROW_OPERATIONS = (Operation.UPSERT, Operation.DELETE)
SCOPE_OPERATIONS = (Operation.SCOPE_GRANTED, Operation.SCOPE_REVOKED)
