"""
Mirroring the declared kinds into `NoticeKindPolicy` after every migrate - not in a migration, so a
project's migrations stay what `makemigrations` writes. A row is added or brought up to date, never
deleted: a kind no longer declared keeps its row, for the notices written under it, and is retired with
`manage.py notices --retire`.
"""

from django.db import DEFAULT_DB_ALIAS, transaction

from isik.django.apps.notifications.kinds import kinds
from isik.django.apps.notifications.swapping import get_kind_policy_model


def mirror_kinds(using=DEFAULT_DB_ALIAS, **kwargs):
    """Writes each declared kind's row - its policy, scope, source and variants as declared. Returns them."""
    rows = []
    with transaction.atomic(using=using):
        for kind in kinds().values():
            row, _created = (
                get_kind_policy_model()
                .objects.using(using)
                .update_or_create(
                    name=kind.name,
                    defaults={
                        "policy": kind.policy,
                        "scope": kind.scope,
                        "source": kind.source,
                        "variants": list(kind.variants),
                    },
                )
            )
            rows.append(row)
    return rows
