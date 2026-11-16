"""
Mirroring the declared kinds into `NoticeKindPolicy` after every migrate - not in a migration, so a
project's migrations stay what `makemigrations` writes. A row is added or brought up to date, never
deleted: a kind no longer declared keeps its row, for the notices written under it, and is retired with
`manage.py notices --retire`.

A kind whose webhook policy moves to `never` keeps every endpoint's subscription to it, inert - fan-out sends
nothing on it - and one moving back off `never` brings them back. Either way, once the mirror commits,
`endpoint_kinds_withdrawn` or `endpoint_kinds_restored` names the endpoints subscribed to it, so a project can
tell their owners.
"""

from functools import partial

from django.apps import apps
from django.db import DEFAULT_DB_ALIAS, transaction

from isik.django.apps.notifications.channels import Channel
from isik.django.apps.notifications.choices import Policy
from isik.django.apps.notifications.kinds import kinds
from isik.django.apps.notifications.signals import endpoint_kinds_restored, endpoint_kinds_withdrawn
from isik.django.apps.notifications.swapping import get_kind_policy_model


AUDIENCE_APP = "isik.django.apps.notifications.audience"


def sent_on_webhook(channels):
    """Whether a kind's mirrored `channels` sends it on the webhook - anything but `never`, there."""
    return channels.get(Channel.WEBHOOK, Policy.NEVER) != Policy.NEVER


def subscribed_endpoints(policy):
    """
    The ids of the endpoints subscribed to the kind `policy` is the row of, as text, in order - read from the
    database the row was.
    """
    if not apps.is_installed(AUDIENCE_APP):
        return []
    from isik.django.apps.notifications.audience.swapping import get_endpoint_kind_model

    rows = get_endpoint_kind_model()._default_manager.using(policy._state.db).filter(kind=policy)
    return sorted({str(endpoint) for endpoint in rows.values_list("endpoint_id", flat=True)})


def _announce(policy, was_sent, using):
    endpoint_ids = subscribed_endpoints(policy)
    if not endpoint_ids:
        return
    signal = endpoint_kinds_withdrawn if was_sent else endpoint_kinds_restored
    send = partial(signal.send, sender=type(policy), kind=policy.name, endpoint_ids=endpoint_ids)
    transaction.on_commit(send, using=using)


def mirror_kinds(using=DEFAULT_DB_ALIAS, **kwargs):
    """Writes each declared kind's row - each channel's policy, scope, source and variants as declared. Returns them."""
    rows = []
    with transaction.atomic(using=using):
        policies = get_kind_policy_model().objects.using(using)
        was_sent = {name: sent_on_webhook(channels) for name, channels in policies.values_list("name", "channels")}
        for kind in kinds().values():
            row, _created = policies.update_or_create(
                name=kind.name,
                defaults={
                    "channels": dict(kind.policies),
                    "scope": kind.scope,
                    "source": kind.source,
                    "variants": list(kind.variants),
                },
            )
            if row.name in was_sent and was_sent[row.name] != sent_on_webhook(row.channels):
                _announce(row, was_sent[row.name], using)
            rows.append(row)
    return rows
