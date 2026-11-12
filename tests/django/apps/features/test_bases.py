"""What each features base gives the project's concrete model when it's prepared: its columns, constraints, triggers."""

import dataclasses

from django.db import models
from django.test.utils import isolate_apps

from isik.django.apps.features import declarations
from isik.django.apps.features.bases import AbstractFeature, AbstractFeatureGrant, AbstractOverrides
from isik.django.apps.features.declarations import Default, OnHolder, OnOverrides, Setting


def a_setting(name, field, own):
    return Setting(
        name=name,
        field=field,
        default=None,
        feature=None,
        without_feature=Default(),
        own=own,
        label="A label",
        description=None,
        visible_to_holder=True,
        request_policies=(),
    )


def described(field):
    return {
        "class": type(field).__name__,
        "null": field.null,
        "blank": field.blank,
        "default": field.default,
        "db_default": field.db_default,
        "help_text": str(field.help_text),
        "db_comment": field.db_comment,
        "max_length": field.max_length,
    }


def nullable(cls, help_text, max_length=None):
    return {
        "class": cls,
        "null": True,
        "blank": True,
        "default": None,
        "db_default": models.NOT_PROVIDED,
        "help_text": help_text,
        "db_comment": help_text,
        "max_length": max_length,
    }


class TestTheOverridesColumns:
    @isolate_apps("tests.testapp")
    def test_each_setting_brings_its_own_and_its_forcing_columns(self, monkeypatch):
        monkeypatch.setattr(
            declarations,
            "settings",
            {
                "limit": a_setting("limit", models.IntegerField(default=3), OnOverrides()),
                "note": a_setting("note", models.TextField(null=True), OnHolder("first_name")),
            },
        )

        class Isolated(AbstractOverrides):
            class Meta:
                app_label = "testapp"
                db_table = "isolated"
                constraints = [models.CheckConstraint(condition=models.Q(id__gt=0), name="isolated_own")]

        fields = {field.name: described(field) for field in Isolated._meta.local_fields}

        assert "note" not in fields
        assert fields["limit"] == nullable("IntegerField", "The holder's own choice, or none.")
        for name in ("limit", "note"):
            assert fields[f"{name}_forced"] == {
                "class": "BooleanField",
                "null": False,
                "blank": False,
                "default": False,
                "db_default": False,
                "help_text": "Whether it's forced, so the holder can't change it.",
                "db_comment": f"Whether {name} is forced - {name}_forced_value then decides.",
                "max_length": None,
            }
            assert fields[f"{name}_forced_source"] == {
                **nullable("CharField", "The force source that forced it, while forced.", 100),
                "default": models.NOT_PROVIDED,
                "db_comment": f"The declared force source that forced {name}, else null.",
            }
            assert fields[f"{name}_forced_reason"] == {
                **nullable("TextField", "Why it was forced, while forced."),
                "default": models.NOT_PROVIDED,
                "db_comment": f"Why {name} was forced, else null.",
            }
        assert fields["limit_forced_value"] == nullable("IntegerField", "What it's forced to, while forced.")
        assert fields["note_forced_value"] == nullable("TextField", "What it's forced to, while forced.")

        def unforced(name):
            return models.Q(
                **{
                    f"{name}_forced": False,
                    f"{name}_forced_value__isnull": True,
                    f"{name}_forced_source__isnull": True,
                    f"{name}_forced_reason__isnull": True,
                }
            )

        def forced(name):
            return models.Q(
                **{
                    f"{name}_forced": True,
                    f"{name}_forced_source__isnull": False,
                    f"{name}_forced_reason__isnull": False,
                }
            )

        expected = [
            models.CheckConstraint(condition=models.Q(id__gt=0), name="isolated_own"),
            # A setting that can't be null can't be forced to null; one that can, can.
            models.CheckConstraint(
                condition=unforced("limit") | (forced("limit") & models.Q(limit_forced_value__isnull=False)),
                name="isolated_limit_forced_exactly",
                violation_error_message=(
                    "A forced limit needs a value, its source and its reason, and an unforced one has none of them."
                ),
                violation_error_code="forced_exactly",
            ),
            models.CheckConstraint(
                condition=unforced("note") | forced("note"),
                name="isolated_note_forced_exactly",
                violation_error_message=(
                    "A forced note needs its source and its reason, and an unforced one has no forced value, source "
                    "or reason."
                ),
                violation_error_code="forced_exactly",
            ),
        ]
        assert Isolated._meta.constraints == expected
        assert Isolated._meta.original_attrs["constraints"] == expected

    @isolate_apps("tests.testapp")
    def test_each_column_is_named_from_the_settings_label(self, monkeypatch):
        labelled = dataclasses.replace(
            a_setting("limit", models.IntegerField(default=3), OnOverrides()), label="Endpoint limit"
        )
        named = dataclasses.replace(
            a_setting("note_text", models.TextField(null=True, verbose_name="Own note"), OnOverrides()), label=None
        )
        monkeypatch.setattr(declarations, "settings", {"limit": labelled, "note_text": named})

        class Named(AbstractOverrides):
            class Meta:
                app_label = "testapp"
                db_table = "named"

        names = {field.name: str(field.verbose_name) for field in Named._meta.local_fields}
        assert {name: value for name, value in names.items() if name.startswith(("limit", "note_text"))} == {
            "limit": "Endpoint limit",
            "limit_forced": "Endpoint limit forced",
            "limit_forced_value": "Endpoint limit forced value",
            "limit_forced_source": "Endpoint limit forced source",
            "limit_forced_reason": "Endpoint limit forced reason",
            "note_text": "Own note",
            "note_text_forced": "note text forced",
            "note_text_forced_value": "note text forced value",
            "note_text_forced_source": "note text forced source",
            "note_text_forced_reason": "note text forced reason",
        }
        assert all(field._verbose_name is not None for field in Named._meta.local_fields if field.name != "id")


class TestTheLedgerTriggers:
    @isolate_apps("tests.testapp")
    def test_a_catalog_and_a_grant_ledger_of_the_projects_own(self):
        class IsolatedFeature(AbstractFeature):
            class Meta:
                app_label = "testapp"

        class IsolatedGrant(AbstractFeatureGrant):
            class Meta:
                app_label = "testapp"

        assert [trigger.name for trigger in IsolatedFeature._meta.triggers] == [
            "protect_feature_delete",
            "protect_feature_truncate",
        ]
        assert [trigger.name for trigger in IsolatedGrant._meta.triggers] == [
            "protect_grant_delete",
            "protect_grant_truncate",
            "protect_grant_revoked",
            "protect_grant_written_revoked",
            "protect_grant_terms",
        ]
