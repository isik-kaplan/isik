"""`senders` - each SMS sender `ISIK['DJANGO']['SMS']` configures, by its alias."""

from django.utils.module_loading import import_string

from isik._internal.translation import gettext as _
from isik.django.conf import django_area
from isik.django.sms.exceptions import InvalidSender, SenderDoesNotExist


KEYS = ("BACKEND", "OPTIONS")


def configured():
    """`ISIK['DJANGO']['SMS']`, `{alias: {"BACKEND": ..., "OPTIONS": {...}}}` - `{}` when unset."""
    return django_area("SMS") or {}


def backend_class(path):
    """The sender class `path` names."""
    from isik.django.sms.backends.base import BaseSMSBackend

    try:
        backend = import_string(path)
    except ImportError as error:
        raise InvalidSender(_("%(path)r can't be imported: %(error)s") % {"path": path, "error": error}) from error
    if not (isinstance(backend, type) and issubclass(backend, BaseSMSBackend)):
        raise InvalidSender(_("%(path)r isn't a BaseSMSBackend.") % {"path": path})
    return backend


class Senders:
    """
    Each configured sender, by alias - `senders["marketing"]`, and `senders.default` for `"default"` - built
    afresh on each lookup, from its `BACKEND` given its `OPTIONS`.
    """

    def __getitem__(self, alias):
        configured_senders = configured()
        if alias not in configured_senders:
            raise SenderDoesNotExist(
                _("ISIK['DJANGO']['SMS'] configures no sender %(alias)r - it configures %(aliases)s.")
                % {"alias": alias, "aliases": ", ".join(map(repr, configured_senders)) or _("none")}
            )
        config = configured_senders[alias]
        return backend_class(config["BACKEND"])(alias=alias, **config.get("OPTIONS", {}))

    @property
    def default(self):
        """The `"default"` sender."""
        return self["default"]

    def __iter__(self):
        return iter(configured())


senders = Senders()


def send_sms(to, body, from_=None, *, using="default", **fields):
    """
    Sends `body` to `to` - a number, or several - through the sender `using`, answering its `SendStatus`, or
    None when a `pre_send` receiver cancelled it. `fields` are the rest of `SMSMessage`'s:

        send_sms("+14155550123", "Your code is 123456")
        send_sms(numbers, "Doors open at 7.", using="marketing", validity=timedelta(hours=1))
    """
    from isik.django.sms.message import SMSMessage

    message = SMSMessage(to, body, from_, using=using, **fields)
    message.send()
    return message.send_status
