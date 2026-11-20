"""The models isik generates are marked, so a project needn't guess them by name."""

import sys
import types

import pytest
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
    from isik.django.apps.common._model_makers import DefaultMakerBase, build_model

    monkeypatch.setattr(generated, "_generated", {})
    host = type("Host", (models.Model,), {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})})

    built = build_model("HostThing", host, fields={}, base_model=DefaultMakerBase)

    assert built.isik_generated == "maker"
    assert generated_models("maker") == [built]


def test_each_is_found_on_the_module_it_names():
    for model in generated_models():
        assert getattr(sys.modules[model.__module__], model.__name__) is model


@isolate_apps("tests.testapp")
def test_a_modules_own_name_is_kept_and_an_earlier_copy_replaced(monkeypatch):
    monkeypatch.setattr(generated, "_generated", {})
    module = types.ModuleType("tests.made_up")
    module.Own = own = object()
    monkeypatch.setitem(sys.modules, "tests.made_up", module)
    meta = type("Meta", (), {"app_label": "testapp"})

    named = mark_generated(type("Own", (models.Model,), {"__module__": "tests.made_up", "Meta": meta}), "lock")
    first = mark_generated(type("Copy", (models.Model,), {"__module__": "tests.made_up", "Meta": meta}), "lock")
    with pytest.warns(RuntimeWarning, match="already registered"):
        again = mark_generated(type("Copy", (models.Model,), {"__module__": "tests.made_up", "Meta": meta}), "lock")

    assert named.__name__ == "Own"
    assert module.Own is own
    assert first is not again
    assert module.Copy is again


@isolate_apps("tests.testapp")
def test_a_module_not_imported_is_left_be(monkeypatch):
    monkeypatch.setattr(generated, "_generated", {})
    meta = type("Meta", (), {"app_label": "testapp"})
    model = type("Nowhere", (models.Model,), {"__module__": "tests.never_imported", "Meta": meta})

    assert mark_generated(model, "maker") is model
    assert "tests.never_imported" not in sys.modules
