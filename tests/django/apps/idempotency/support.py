"""Helpers the idempotency tests share."""

from rest_framework.test import APIClient

from tests.testapp.models import EmailUser


def make_user(name):
    return EmailUser.objects.create(username=name, email=f"{name}@example.com")


def client_for(user, **kwargs):
    client = APIClient(**kwargs)
    client.force_authenticate(user)
    return client


def post(client, url, data=None, key=None, **extra):
    """POSTs `data` as JSON, sending `key` as the Idempotency-Key when given."""
    headers = {} if key is None else {"Idempotency-Key": str(key)}
    return client.post(url, data, format="json", headers=headers, **extra)
