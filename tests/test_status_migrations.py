"""The two migrations turning a revoke or a retire stamped as a time into a status - for rows written before."""

import importlib
import types

import pytest


CONVERSIONS = [
    (
        "isik.django.apps.features.migrations.0004_status_and_history",
        "grants_revoked_before_stay_revoked",
        "isik_features",
        "featuregrant",
        "revoked_at",
        "revoked",
    ),
    (
        "isik.django.apps.notifications.migrations.0002_status_and_history",
        "kinds_retired_before_stay_retired",
        "isik_notifications",
        "noticekindpolicy",
        "retired_at",
        "retired",
    ),
]


def model(swapped, updates):
    def update(**values):
        updates.append(("update", values))
        return 1

    def filter_(**lookups):
        updates.append(("filter", lookups))
        return types.SimpleNamespace(update=update)

    return types.SimpleNamespace(
        _meta=types.SimpleNamespace(swapped=swapped), _default_manager=types.SimpleNamespace(filter=filter_)
    )


@pytest.mark.parametrize(("module", "function", "app", "name", "stamp", "status"), CONVERSIONS)
class TestTheConversion:
    def test_a_stamped_row_takes_its_status(self, module, function, app, name, stamp, status):
        updates, asked = [], []
        found = model(None, updates)
        apps = types.SimpleNamespace(get_model=lambda *args: asked.append(args) or found)

        getattr(importlib.import_module(module), function)(apps, None)

        assert asked == [(app, name)]
        assert updates == [("filter", {f"{stamp}__isnull": False}), ("update", {"status": status})]

    def test_a_swapped_out_model_is_left_to_the_project(self, module, function, app, name, stamp, status):
        updates = []
        apps = types.SimpleNamespace(get_model=lambda *args: model("owned.Thing", updates))

        getattr(importlib.import_module(module), function)(apps, None)

        assert updates == []
