from unittest.mock import call, patch

from isik.django.apps.common.apps import CommonConfig


def test_name_points_at_the_app_package():
    assert CommonConfig.name == "isik.django.apps.common"


def test_modules_to_initialize_lists_the_lookups_module():
    assert "isik.django.apps.common.db.lookups" in CommonConfig.modules_to_initialize


def test_ready_imports_every_module_in_modules_to_initialize():
    # ready() only touches modules_to_initialize and importlib - no need for a fully
    # constructed AppConfig (which normally requires Django's app registry machinery).
    app_config = object.__new__(CommonConfig)

    with patch("isik.django.apps.common.apps.importlib.import_module") as import_module:
        app_config.ready()

    assert import_module.call_args_list == [call(module) for module in CommonConfig.modules_to_initialize]
