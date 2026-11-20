import json

from django.core.management.base import BaseCommand, CommandError

from isik._internal.translation import gettext as _
from isik.django.apps.notifications import pipeline
from isik.django.apps.notifications.channels import Channel
from isik.django.apps.notifications.choices import KindStatus, Policy
from isik.django.apps.notifications.conf import in_channel_order
from isik.django.apps.notifications.kinds import kinds
from isik.django.apps.notifications.swapping import get_kind_policy_model
from isik.django.apps.notifications.sync import sent_on_webhook, subscribed_endpoints


def describe(kind):
    """What `kind` is, as plain values - what the listing shows of it."""
    return {
        "kind": kind.name,
        "source": str(kind.source),
        "model": kind.model._meta.label if kind.model is not None else None,
        "trigger": kind.trigger_name if kind.model is not None else None,
        "on": str(kind.on) if kind.on is not None else None,
        # The SQL the trigger tests - what any condition comes to, whatever it prints as.
        "condition": str(kind.condition.resolve(kind.model)) if kind.condition is not None else None,
        "variants": list(kind.variants),
        "audience": str(kind.audience) if kind.audience is not None else None,
        "scope": str(kind.scope),
        "channels": {channel: str(policy) for channel, policy in kind.policies.items()},
        # Each channel the deployment's CHANNEL_DEFAULTS or CHANNEL_FORCED set, rather than the kind.
        "set_by": {
            channel: kind.policy_set_by(channel)
            for channel in kind.policies
            if kind.policy_set_by(channel) != "declared"
        },
        "snapshot": list(kind.snapshot),
    }


def inert_endpoints():
    """{kind: [endpoint id, ...]} - each kind whose policy now leaves endpoints' subscriptions to it inert."""
    policies = get_kind_policy_model().objects.order_by("name")
    return {
        policy.name: endpoints
        for policy in policies
        if not sent_on_webhook(policy.channels) and (endpoints := subscribed_endpoints(policy))
    }


def pending():
    """
    [{"kind", "channel", "now", "after", "withdrawn"}] - each channel a declared kind's mirrored row is sent on under
    another policy than its declaration and the deployment's say: what the next migrate changes, and how many
    endpoints' subscriptions it leaves inert, a kind leaving webhook. What's sent changes then, not before.
    """
    changes = []
    for policy in get_kind_policy_model().objects.filter(name__in=list(kinds())).order_by("name"):
        after = {channel: str(each) for channel, each in kinds()[policy.name].policies.items()}
        for channel in in_channel_order({**policy.channels, **after}):
            now, then = policy.channels.get(channel, Policy.NEVER), after.get(channel, Policy.NEVER)
            if now != then:
                # A move to never on webhook - from a policy it's sent under - leaves its subscriptions inert.
                leaves = channel == Channel.WEBHOOK and then == Policy.NEVER
                changes.append(
                    {
                        "kind": policy.name,
                        "channel": channel,
                        "now": now,
                        "after": then,
                        "withdrawn": len(subscribed_endpoints(policy)) if leaves else 0,
                    }
                )
    return changes


def drift():
    """{"unmirrored": [...], "undeclared": [...], "retired": [...]} - where the declarations and the mirror differ."""
    declared = set(kinds())
    rows = dict(get_kind_policy_model().objects.values_list("name", "status"))
    return {
        "unmirrored": sorted(declared - set(rows)),
        "undeclared": sorted(
            name for name, status in rows.items() if name not in declared and status == KindStatus.ACTIVE
        ),
        "retired": sorted(name for name, status in rows.items() if name in declared and status == KindStatus.RETIRED),
    }


def _shown(value):
    """`value` as a line shows it - a list's items, a mapping's `key=value` pairs, each comma-separated."""
    if isinstance(value, dict):
        return ", ".join(f"{key}={each}" for key, each in value.items())
    return ", ".join(value) if isinstance(value, list) else value


def _until_done(step):
    """Runs `step` until it does nothing; how much it did in all."""
    total = 0
    while done := step():
        total += done
    return total


class Command(BaseCommand):
    help = (
        "Lists every declared notice kind - what writes it, what it watches, who gets it and on which channels - "
        "where the declarations and the mirrored policy rows differ, and the endpoints whose subscriptions a kind's "
        "policy leaves inert. --retire retires a kind."
    )

    def add_arguments(self, parser):
        parser.add_argument("--format", choices=["text", "json"], default="text")
        parser.add_argument(
            "--fan-out",
            action="store_true",
            help="Fan out every waiting notice now.",
        )
        parser.add_argument(
            "--work",
            action="store_true",
            help=(
                "What a beat would run, once: fan out every waiting notice, take back what's stranded or overdue, "
                "and prune what's past its retention - for a cron, in a project with no beat."
            ),
        )
        parser.add_argument(
            "--retire",
            metavar="KIND",
            help="Retire KIND: its trigger writes nothing more, and its row and notices stay.",
        )

    def handle(self, *args, format, retire, fan_out, work, **options):
        if retire is not None:
            self._retire(retire)
            return
        if fan_out:
            self._fan_out()
            return
        if work:
            self._work()
            return
        described, differing, inert, changing = (
            [describe(kind) for kind in sorted(kinds().values(), key=lambda kind: kind.name)],
            drift(),
            inert_endpoints(),
            pending(),
        )
        if format == "json":
            self.stdout.write(
                json.dumps({"kinds": described, "drift": differing, "inert": inert, "pending": changing}, indent=2)
            )
            return
        for kind in described:
            self.stdout.write(kind["kind"])
            for key, value in kind.items():
                if key != "kind" and value not in (None, [], {}):
                    self.stdout.write(f"  {key}: {_shown(value)}")
        labels = {
            "unmirrored": _("Declared, with no policy row - run migrate"),
            "undeclared": _("A policy row no declaration makes - retire it"),
            "retired": _("Declared, and retired"),
        }
        for key, names in differing.items():
            if names:
                self.stdout.write(f"{labels[key]}: {', '.join(names)}")
        for change in changing:
            line = _("%(kind)s on %(channel)s: %(now)s now, %(after)s after the next migrate") % change
            if change["withdrawn"]:
                line += _(" - %(withdrawn)s endpoints' subscriptions to it go inert") % change
            self.stdout.write(line)
        for kind, endpoints in inert.items():
            self.stdout.write(
                _("%(kind)s isn't sent on webhook - %(count)s endpoints' subscriptions to it are inert: %(ids)s")
                % {"kind": kind, "count": len(endpoints), "ids": ", ".join(endpoints)}
            )

    def _fan_out(self):
        self.stdout.write(_("Fanned out %(count)s notices.") % {"count": _until_done(pipeline.fan_out)})

    def _work(self):
        self._fan_out()
        reaped = pipeline.reap_stranded()
        self.stdout.write(_("Asked again for %(deliveries)s deliveries and %(shards)s shards.") % reaped)
        pruned = {"attempts": _until_done(pipeline.prune_attempts), "notices": _until_done(pipeline.prune_notices)}
        self.stdout.write(_("Pruned %(attempts)s attempts and %(notices)s notices.") % pruned)

    def _retire(self, name):
        policies = get_kind_policy_model().objects
        retired = policies.filter(name=name, status=KindStatus.ACTIVE).update(status=KindStatus.RETIRED)
        if not retired:
            raise CommandError(_("No unretired kind is called %(kind)s.") % {"kind": name})
        self.stdout.write(_("Retired %(kind)s.") % {"kind": name})
