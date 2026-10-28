import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from isik._internal.translation import gettext as _
from isik.django.apps.notifications import pipeline
from isik.django.apps.notifications.kinds import kinds
from isik.django.apps.notifications.swapping import get_kind_policy_model


def describe(kind):
    """What `kind` is, as plain values - what the listing shows of it."""
    return {
        "kind": kind.name,
        "source": str(kind.source),
        "model": kind.model._meta.label if kind.model is not None else None,
        "trigger": kind.trigger_name if kind.model is not None else None,
        "on": str(kind.on) if kind.on is not None else None,
        "condition": repr(kind.condition) if kind.condition is not None else None,
        "variants": list(kind.variants),
        "audience": str(kind.audience) if kind.audience is not None else None,
        "policy": str(kind.policy),
        "scope": str(kind.scope),
        "channels": list(kind.enabled_channels),
        "snapshot": list(kind.snapshot),
    }


def drift():
    """{"unmirrored": [...], "undeclared": [...], "retired": [...]} - where the declarations and the mirror differ."""
    declared = set(kinds())
    rows = dict(get_kind_policy_model().objects.values_list("kind", "retired_at"))
    return {
        "unmirrored": sorted(declared - set(rows)),
        "undeclared": sorted(name for name, retired_at in rows.items() if name not in declared and retired_at is None),
        "retired": sorted(name for name, retired_at in rows.items() if name in declared and retired_at is not None),
    }


def _until_done(step):
    """Runs `step` until it does nothing; how much it did in all."""
    total = 0
    while done := step():
        total += done
    return total


class Command(BaseCommand):
    help = (
        "Lists every declared notice kind - what writes it, what it watches, who gets it and on which channels - "
        "and where the declarations and the mirrored policy rows differ. --retire retires a kind."
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
        described, differing = (
            [describe(kind) for kind in sorted(kinds().values(), key=lambda kind: kind.name)],
            drift(),
        )
        if format == "json":
            self.stdout.write(json.dumps({"kinds": described, "drift": differing}, indent=2))
            return
        for kind in described:
            self.stdout.write(kind["kind"])
            for key, value in kind.items():
                if key != "kind" and value not in (None, []):
                    self.stdout.write(f"  {key}: {', '.join(value) if isinstance(value, list) else value}")
        labels = {
            "unmirrored": _("Declared, with no policy row - run migrate"),
            "undeclared": _("A policy row no declaration makes - retire it"),
            "retired": _("Declared, and retired"),
        }
        for key, names in differing.items():
            if names:
                self.stdout.write(f"{labels[key]}: {', '.join(names)}")

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
        retired = policies.filter(kind=name, retired_at__isnull=True).update(retired_at=timezone.now())
        if not retired:
            raise CommandError(_("No unretired kind is called %(kind)s.") % {"kind": name})
        self.stdout.write(_("Retired %(kind)s.") % {"kind": name})
