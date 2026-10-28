import pytest

from tests.testapp.models import EmailUser, Parcel


@pytest.fixture
def alice(db):
    return EmailUser.objects.create(username="alice", email="alice@example.com")


@pytest.fixture
def bob(db):
    return EmailUser.objects.create(username="bob", email="bob@example.com")


@pytest.fixture
def parcel(alice, bob):
    return Parcel.objects.create(sender=alice, recipient=bob)


@pytest.fixture
def dispatched(monkeypatch):
    """The tasks the pipeline asked for, in order, instead of a broker: [(task name, args, kwargs)]."""
    from isik.django.apps.notifications import tasks

    asked = []
    for name in ("send", "fan_out_shard", "fan_out"):
        task = getattr(tasks, name)
        monkeypatch.setattr(task, "delay", lambda *args, _name=name, **kwargs: asked.append((_name, args, kwargs)))
        monkeypatch.setattr(
            task,
            "apply_async",
            lambda args=None, kwargs=None, _name=name, **options: asked.append((_name, tuple(args or ()), options)),
        )
    return asked


@pytest.fixture
def declaring(monkeypatch):
    """Replaces the declared kinds with the ones given - their names, each once, the vocabulary."""
    from isik.django.apps.notifications import kinds

    def declare(*declared):
        monkeypatch.setattr(kinds, "declared", list(declared))
        names = {kind.name: kinds.KindName(kind.name, kind.variants) for kind in declared}
        monkeypatch.setattr(kinds, "vocabulary", list(names.values()))

    return declare
