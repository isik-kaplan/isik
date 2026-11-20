from django.apps import AppConfig

from isik._internal.translation import gettext_lazy


class AccountsConfig(AppConfig):
    name = "isik.django.apps.accounts"
    label = "isik_accounts"
    verbose_name = gettext_lazy("Accounts")

    def ready(self):
        from isik.django.apps.accounts import checks  # noqa: F401 - registers the checks
        from isik.django.apps.accounts.erasures import import_erasures_module, register_isiks_own

        register_isiks_own()
        import_erasures_module()
