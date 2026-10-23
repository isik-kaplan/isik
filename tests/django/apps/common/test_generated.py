"""The models isik generates are marked, so a project needn't guess them by name."""

from django.db import models
from django.test.utils import isolate_apps

from isik.django.apps.common import generated
from isik.django.apps.common.generated import KINDS, generated_models, mark_generated
from tests.testapp.models import Team, Widget, WidgetEvent


def test_the_kinds():
    assert KINDS == ("event", "maker", "lock")


def test_a_tracked_models_events_are_marked():
    assert WidgetEvent.isik_generated == "event"
    assert WidgetEvent in generated_models("event")


def test_a_makers_models_are_marked():
    makers = generated_models("maker")

    assert makers
    assert all(model.isik_generated == "maker" for model in makers)


def test_a_versions_lock_proxies_are_marked():
    locks = {model.__name__ for model in generated_models("lock")}

    assert "TeamMembersConcurrencyLock" in locks


def test_a_projects_own_models_arent():
    assert not hasattr(Widget, "isik_generated")
    assert Widget not in generated_models()
    assert Team not in generated_models()


def test_every_kind_without_one_named():
    assert set(generated_models()) == {
        *generated_models("event"),
        *generated_models("maker"),
        *generated_models("lock"),
    }


@isolate_apps("tests.testapp")
def test_marking_one_again_replaces_it(monkeypatch):
    monkeypatch.setattr(generated, "_generated", {})
    meta = type("Meta", (), {"app_label": "testapp"})
    first = type("Again", (models.Model,), {"__module__": __name__, "Meta": meta})

    assert mark_generated(first, "maker") is first
    mark_generated(first, "event")

    assert generated_models() == [first]
    assert generated_models("event") == [first]
    assert generated_models("maker") == []


@isolate_apps("tests.testapp")
def test_a_model_a_maker_builds_is_a_maker(monkeypatch):
    from isik.django.apps.common._model_makers import build_model

    monkeypatch.setattr(generated, "_generated", {})
    host = type("Host", (models.Model,), {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})})

    built = build_model("HostThing", host, fields={}, base_model=models.Model)

    assert built.isik_generated == "maker"
    assert generated_models("maker") == [built]
