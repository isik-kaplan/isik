"""
`isik.E028` - an inbound setting or source declaration that can't work: see `inbound_problems()`.
"""

from datetime import timedelta

from django.core.checks import Error, Tags

from isik._internal.translation import gettext as _
from isik.django.apps.common.checks import Check, isik_check
from isik.django.apps.inbound import conf
from isik.django.apps.inbound.sources import handlers, sources
from isik.django.apps.inbound.swapping import DEFAULTS as SWAPPABLE
from isik.django.conf import swappable_keys


AREA = "ISIK['DJANGO']['INBOUND']"


def _is_period(value):
    return isinstance(value, timedelta) and value > timedelta(0)


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def setting_problems():
    """What's wrong with `ISIK['DJANGO']['INBOUND']`, one sentence each."""
    problems = []
    unknown = sorted(set(conf.configured()) - set(conf.DEFAULTS) - swappable_keys(SWAPPABLE, "INBOUND"))
    if unknown:
        problems.append(_("%(area)s has keys it doesn't know: %(keys)s.") % {"area": AREA, "keys": ", ".join(unknown)})
    backoff = conf.setting("BACKOFF")
    if not isinstance(backoff, (list, tuple)) or not backoff or not all(_is_count(delay) for delay in backoff):
        problems.append(
            _("%(area)s['BACKOFF'] is the seconds to wait after each failed attempt, such as (60, 300, 1800).")
            % {"area": AREA}
        )
    if not _is_period(conf.setting("STRANDED_AFTER")):
        problems.append(
            _("%(area)s['STRANDED_AFTER'] is how long a worker may hold an event before it's taken back, a timedelta.")
            % {"area": AREA}
        )
    for name in ("MAX_ATTEMPTS", "BATCH"):
        if conf.setting(name) is not None and not _is_count(conf.setting(name)):
            problems.append(_("%(area)s['%(name)s'] is at least 1, or None.") % {"area": AREA, "name": name})
    retention = conf.setting("RETENTION")
    if retention is not None and not _is_period(retention):
        problems.append(_("%(area)s['RETENTION'] is a timedelta longer than none, or None.") % {"area": AREA})
    if (conf.setting("PARTITIONS") is None) != (conf.setting("IN_PARTITION") is None):
        problems.append(_("%(area)s sets one of PARTITIONS and IN_PARTITION - set both, or neither.") % {"area": AREA})
    return problems


def source_problems():
    """What's wrong with the declared sources and handlers, one sentence each."""
    problems = []
    for name, source in sources.items():
        if not callable(getattr(source.verifier, "verify", None)):
            problems.append(_("%(source)s's verifier has no verify(request).") % {"source": name})
        if source.identifies_events and not callable(source.event_id):
            problems.append(
                _("%(source)s's event_id is f(request, payload), or Unidentified(reason=...).") % {"source": name}
            )
        if not callable(source.event_type):
            problems.append(_("%(source)s's event_type is f(request, payload).") % {"source": name})
        if conf.partitioned() and source.partition is None:
            problems.append(
                _(
                    "%(source)s names no partition, and %(area)s sets partitions - say which one a request is for "
                    "with partition=, read before anything is written."
                )
                % {"source": name, "area": AREA}
            )
        if not conf.partitioned() and source.partition is not None:
            problems.append(
                _("%(source)s names a partition, and %(area)s sets no PARTITIONS and IN_PARTITION to enter it by.")
                % {"source": name, "area": AREA}
            )
    for source in sorted({source for source, _type in handlers if source not in sources}):
        problems.append(_("A handler takes %(source)s's events, and no source is called that.") % {"source": source})
    return problems


@isik_check(Check.INBOUND, Tags.models)
def inbound_can_work(options, **kwargs):
    """An inbound setting or declaration that can't work - see `setting_problems()` and `source_problems()`."""
    hint = _("See isik.django.apps.inbound.")
    return [Error(problem, hint=hint, id="isik.E028") for problem in setting_problems() + source_problems()]
