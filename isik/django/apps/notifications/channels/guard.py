"""
Where a webhook may connect: only to a public address, unless `ALLOW_PRIVATE_TARGETS` says otherwise. An
endpoint is a URL a tenant gives - left alone, one naming the database's host, a metadata service
(169.254.169.254, 100.100.100.200) or anything else on the workers' network has the worker reach it for
them.

The address is checked where it's connected to: the transport resolves the host once, checks that address
and connects to it, so a name resolving to a public address when checked and a private one a moment later
gets nowhere. The URL's host still names the TLS server and the Host header. Redirects aren't followed - a
redirect is a second way round - and the environment's proxy isn't used. Through `WEBHOOK_PROXY`, only the
address isik resolves itself can be checked, since the proxy resolves again: the proxy has to keep its own
egress to what it should reach.
"""

import ipaddress
import logging
import socket

from isik._internal import check_extra
from isik._internal.translation import gettext as _
from isik.django.apps.notifications import conf


# httpcore is httpx's own dependency, used here directly: an install missing either is told which extra to fix.
check_extra("notifications", "httpx")
check_extra("notifications", "httpcore")

import httpcore  # noqa: E402
import httpx  # noqa: E402


logger = logging.getLogger(__name__)

# Each kind of address refused, by what the refusal tells the endpoint's owner. The address itself is never
# told: one refusal per address would map the workers' network for whoever registers endpoints.
REFUSALS = {
    "loopback": _("The endpoint is refused: it resolves to a loopback address."),
    "link-local": _("The endpoint is refused: it resolves to a link-local address."),
    "private": _("The endpoint is refused: it resolves to a private address."),
    "non-public": _("The endpoint is refused: it resolves to an address that isn't public."),
}


class RefusedTarget(Exception):
    """A webhook's address isn't one it may reach - its message names what kind, never the address."""

    def __init__(self, category):
        self.category = category
        super().__init__(REFUSALS[category])


def category_of(ip):
    """What kind of non-public address `ip` is."""
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"
    if ip.is_private:
        return "private"
    return "non-public"


def check(address):
    """Raises `RefusedTarget` unless a webhook may connect to `address`, an IP address as text."""
    ip = ipaddress.ip_address(address)
    # ::ffff:10.0.0.1 is 10.0.0.1 - judged as the IPv4 address it is.
    ip = getattr(ip, "ipv4_mapped", None) or ip
    if ip.is_global:
        return
    allowed = conf.setting("ALLOW_PRIVATE_TARGETS")
    if allowed is True or any(ip in ipaddress.ip_network(network) for network in allowed or ()):
        return
    logger.warning("A webhook to %s was refused: it isn't a public address.", address)
    raise RefusedTarget(category_of(ip))


def resolve(host, port):
    """The first address `host` resolves to - raising httpcore's `ConnectError` when it resolves to none."""
    try:
        return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0][4][0]
    except socket.gaierror as error:
        raise httpcore.ConnectError(str(error)) from error


class GuardedBackend(httpcore.SyncBackend):
    """Connects to the address the host resolved to and was checked as - and to no other."""

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        address = resolve(host, port)
        check(address)
        return super().connect_tcp(address, port, timeout, local_address, socket_options)


def transport():
    """The transport webhooks go out on: through `WEBHOOK_PROXY` when it's set, else connecting only where
    `check()` allows."""
    proxy = conf.setting("WEBHOOK_PROXY")
    if proxy:
        return httpx.HTTPTransport(proxy=proxy)
    guarded = httpx.HTTPTransport()
    # httpx builds its pool with httpcore's own backend; this one is the same pool on the guarded backend.
    guarded._pool = httpcore.ConnectionPool(network_backend=GuardedBackend())
    return guarded


def check_url(url):
    """Raises `RefusedTarget` for a proxied webhook whose host resolves where it may not reach - the most
    that can be checked when the proxy resolves it again itself."""
    parsed = httpx.URL(url)
    check(resolve(parsed.host, parsed.port or (443 if parsed.scheme == "https" else 80)))
