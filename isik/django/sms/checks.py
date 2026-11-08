"""
The SMS senders' system checks, run where `ISIK['DJANGO']['SMS']` is set: `isik.E024` for a sender configured
wrong, and `isik.W010` for one sending nothing anywhere while DEBUG is off.
"""

from django.conf import settings

from isik._internal.translation import gettext as _
from isik.django.sms.backends.base import COMMON_OPTIONS
from isik.django.sms.exceptions import InvalidSender
from isik.django.sms.senders import KEYS, backend_class, configured


def _sender_problems(alias, config):
    where = f"ISIK['DJANGO']['SMS'][{alias!r}]"
    if not isinstance(config, dict):
        return [_("%(where)s is %(value)r, not a dict.") % {"where": where, "value": config}], None
    unknown = sorted(set(config) - set(KEYS), key=str)
    if unknown:
        return [
            _("%(where)s has keys it doesn't know: %(keys)s - it holds BACKEND and OPTIONS.")
            % {"where": where, "keys": ", ".join(map(repr, unknown))}
        ], None
    if not isinstance(config.get("BACKEND"), str):
        return [_("%(where)s names no BACKEND - the dotted path of its sender.") % {"where": where}], None
    options = config.get("OPTIONS", {})
    if not isinstance(options, dict):
        return [_("%(where)s's OPTIONS is %(value)r, not a dict.") % {"where": where, "value": options}], None
    try:
        backend = backend_class(config["BACKEND"])
    except InvalidSender as error:
        return [_("%(where)s: %(error)s") % {"where": where, "error": error}], None
    problems = []
    unknown = sorted(set(options) - set(COMMON_OPTIONS) - set(backend.options))
    if unknown and not backend.takes_any_option:
        problems.append(
            _("%(where)s's OPTIONS has %(options)s, which %(backend)s doesn't take.")
            % {"where": where, "options": ", ".join(unknown), "backend": backend.__name__}
        )
    problems.extend(
        _("%(where)s: %(problem)s") % {"where": where, "problem": problem} for problem in backend.check(options)
    )
    return problems, backend


def sender_problems():
    """`(errors, warnings)` - what's wrong with each sender `ISIK['DJANGO']['SMS']` configures, as messages."""
    senders = configured()
    if not isinstance(senders, dict):
        return [_("ISIK['DJANGO']['SMS'] is %(value)r, not a dict of senders.") % {"value": senders}], []
    errors, warnings = [], []
    for alias, config in senders.items():
        problems, backend = _sender_problems(alias, config)
        errors.extend(problems)
        if backend is not None and backend.sends_nowhere and not settings.DEBUG:
            warnings.append(
                _("ISIK['DJANGO']['SMS'][%(alias)r] is %(backend)s, which sends no SMS anywhere - and DEBUG is off.")
                % {"alias": alias, "backend": config["BACKEND"]}
            )
    return errors, warnings
