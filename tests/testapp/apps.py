from django.apps import AppConfig


class TestAppConfig(AppConfig):
    name = "tests.testapp"
    label = "testapp"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from tests.testapp import sync_views  # noqa: F401 - registers each synced model's viewset
