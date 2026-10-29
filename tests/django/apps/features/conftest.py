import pytest

from isik.django.apps.features.resolution import forget
from tests.testapp.models import EmailUser


@pytest.fixture
def alice(db):
    return EmailUser.objects.create(username="alice", email="alice@example.com", first_name="Alice")


@pytest.fixture
def bob(db):
    return EmailUser.objects.create(username="bob", email="bob@example.com")


@pytest.fixture
def fresh():
    """Reads `holder` again from the database - a holder's cache lives on its instance."""

    def read(holder):
        forget(holder)
        return holder

    return read


@pytest.fixture
def heard():
    """Every setting_changed and feature_changed sent while the test runs."""
    from isik.django.apps.features.signals import feature_changed, setting_changed

    sent = []

    # Each sent by the holder's own class, so a receiver can listen for one holder model.
    def on_setting(sender, holder, name, before, after, **kwargs):
        assert sender is type(holder)
        sent.append(("setting", holder.pk, name, before.value, after.value))

    def on_feature(sender, holder, feature, held, **kwargs):
        assert sender is type(holder)
        sent.append(("feature", holder.pk, feature, held))

    setting_changed.connect(on_setting)
    feature_changed.connect(on_feature)
    yield sent
    setting_changed.disconnect(on_setting)
    feature_changed.disconnect(on_feature)
