"""Where a webhook may connect: each kind of address refused, checked where it's connected, and through a proxy."""

import logging
import socket

import httpcore
import httpx
import pytest

from isik.django.apps.notifications.channels import guard
from isik.django.apps.notifications.channels.guard import GuardedBackend, RefusedTarget, check


class TestWhatIsRefused:
    @pytest.mark.parametrize(
        ("address", "category"),
        [
            ("127.0.0.1", "loopback"),
            ("::1", "loopback"),
            ("169.254.169.254", "link-local"),
            ("fe80::1", "link-local"),
            ("10.0.0.5", "private"),
            ("172.18.0.5", "private"),
            ("192.168.1.1", "private"),
            ("fd00::1", "private"),
            ("::ffff:10.0.0.1", "private"),
            ("100.100.100.200", "non-public"),
            ("0.0.0.0", "private"),
        ],
    )
    def test_every_address_that_isnt_public(self, address, category):
        with pytest.raises(RefusedTarget) as refused:
            check(address)

        assert refused.value.category == category
        assert str(refused.value) == guard.REFUSALS[category]
        assert address not in str(refused.value)

    @pytest.mark.parametrize("address", ["93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c", "::ffff:8.8.8.8"])
    def test_a_public_address_is_taken(self, address):
        check(address)

    def test_the_refusal_names_the_kind_of_address_alone(self):
        assert guard.REFUSALS == {
            "loopback": "The endpoint is refused: it resolves to a loopback address.",
            "link-local": "The endpoint is refused: it resolves to a link-local address.",
            "private": "The endpoint is refused: it resolves to a private address.",
            "non-public": "The endpoint is refused: it resolves to an address that isn't public.",
        }

    def test_the_operator_hears_the_address(self, caplog):
        with caplog.at_level(logging.WARNING), pytest.raises(RefusedTarget):
            check("10.0.0.5")

        assert [record.getMessage() for record in caplog.records] == [
            "A webhook to 10.0.0.5 was refused: it isn't a public address."
        ]


class TestAllowingPrivateTargets:
    def test_every_one(self, settings):
        settings.ISIK_NOTIFICATIONS = {"ALLOW_PRIVATE_TARGETS": True}

        check("10.0.0.5")
        check("127.0.0.1")

    def test_the_networks_named(self, settings):
        settings.ISIK_NOTIFICATIONS = {"ALLOW_PRIVATE_TARGETS": ["10.1.0.0/16", "fd00::/8"]}

        check("10.1.2.3")
        check("fd00::1")
        with pytest.raises(RefusedTarget):
            check("10.2.0.1")


class TestConnecting:
    def test_to_the_address_checked_and_no_other(self, monkeypatch):
        connected = []
        monkeypatch.setattr(
            socket, "getaddrinfo", lambda host, port, type: [(socket.AF_INET, type, 6, "", ("93.184.215.14", port))]
        )
        monkeypatch.setattr(
            httpcore.SyncBackend,
            "connect_tcp",
            lambda self, host, port, timeout=None, local_address=None, socket_options=None: connected.append(
                (host, port, timeout)
            ),
        )

        GuardedBackend().connect_tcp("hooks.example", 443, timeout=3)

        assert connected == [("93.184.215.14", 443, 3)]

    def test_a_name_resolving_to_a_private_address_is_never_connected(self, monkeypatch):
        connected = []
        monkeypatch.setattr(
            socket, "getaddrinfo", lambda host, port, type: [(socket.AF_INET, type, 6, "", ("10.0.0.5", port))]
        )
        monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda *args, **kwargs: connected.append(args))

        with pytest.raises(RefusedTarget):
            GuardedBackend().connect_tcp("rebound.example", 443)
        assert connected == []

    def test_a_name_resolving_to_nothing_fails_to_connect(self, monkeypatch):
        def unresolvable(*args, **kwargs):
            raise socket.gaierror("no such name")

        monkeypatch.setattr(socket, "getaddrinfo", unresolvable)

        with pytest.raises(httpcore.ConnectError, match="no such name"):
            guard.resolve("nowhere.example", 80)

    def test_a_real_request_to_loopback_is_refused_before_it_connects(self):
        with httpx.Client(transport=guard.transport()) as client, pytest.raises(RefusedTarget) as refused:
            client.post("http://127.0.0.1:9/hook")

        assert refused.value.category == "loopback"

    def test_the_guarded_pool_is_the_transports_own(self):
        transport = guard.transport()

        assert isinstance(transport._pool._network_backend, GuardedBackend)


class TestThroughAProxy:
    def test_the_proxy_carries_it(self, settings):
        settings.ISIK_NOTIFICATIONS = {"WEBHOOK_PROXY": "http://proxy.internal:3128"}

        transport = guard.transport()

        assert isinstance(transport._pool, httpcore.HTTPProxy)

    def test_only_the_address_resolved_here_can_be_checked(self, monkeypatch):
        asked = []
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda host, port, type: asked.append((host, port)) or [(socket.AF_INET, type, 6, "", ("127.0.0.1", port))],
        )

        with pytest.raises(RefusedTarget):
            guard.check_url("https://hooks.example/in")
        with pytest.raises(RefusedTarget):
            guard.check_url("http://hooks.example:8080/in")
        with pytest.raises(RefusedTarget):
            guard.check_url("http://hooks.example/in")

        assert asked == [("hooks.example", 443), ("hooks.example", 8080), ("hooks.example", 80)]
