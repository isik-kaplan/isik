"""The test project's rows of a person: the tombstone an anonymized account leaves, and a widget's name."""

from isik.django.apps.accounts.erasures import erasure


erasure(
    "testapp.EmailUser",
    anonymize={
        "email": lambda user: f"deleted-{user.pk}@example.invalid",
        "username": lambda user: f"deleted-{user.pk}",
        "first_name": "",
        "last_name": "",
    },
)
erasure("testapp.Widget", via="owner", anonymize={"name": "a deleted person's widget"})
