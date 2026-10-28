"""The partition resolvers - the SQL each compiles to off the models, what each refuses, and one installed."""

import dataclasses
import uuid
from contextlib import nullcontext

import pgtrigger
import pytest
from django.db import models
from django.test.utils import isolate_apps

from isik.django.apps.notifications import kinds
from isik.django.apps.notifications.audiences import Field
from isik.django.apps.notifications.checks import kind_problems, setting_problems
from isik.django.apps.notifications.kinds import Kind
from isik.django.apps.notifications.models import Notice, Policy
from isik.django.apps.notifications.partitions import Column, Connection, Expression, Follow, PartitionSource
from tests.testapp.models import EmailUser, Parcel


ROW = "CASE WHEN TG_OP = 'DELETE' THEN OLD.{0} ELSE NEW.{0} END"


def kind(**changes):
    return Kind(
        **{
            "name": "k",
            "audience": Field("sender"),
            "policy": Policy.MANDATORY,
            "model": Parcel,
            "on": pgtrigger.Update,
            **changes,
        }
    )


class TestWhatEachCompilesTo:
    def test_the_connections(self):
        assert Connection().resolve(Parcel) == "current_schema()"

    def test_a_column_of_the_row(self):
        assert Column("status").resolve(Parcel) == ROW.format('"status"')

    def test_a_key_followed_to_another_rows_column(self):
        assert Follow("sender", "username").resolve(Parcel) == (
            f'(SELECT "username" FROM "testapp_emailuser" WHERE "id" = {ROW.format(chr(34) + "sender_id" + chr(34))})'
        )

    def test_an_expression_of_the_projects(self):
        assert Expression(lambda model: f"'{model._meta.db_table}'").resolve(Parcel) == "'testapp_parcel'"

    def test_the_base_has_nothing_to_compile(self):
        with pytest.raises(NotImplementedError):
            PartitionSource().resolve(Parcel)
        assert PartitionSource().check(Parcel, kind()) == []

    def test_how_each_reads(self):
        def build(model):
            return "x"

        assert [
            repr(Connection()),
            repr(Column("a")),
            repr(Follow("k", "f")),
            repr(Follow("k", "f", allow_null=True)),
            repr(Expression(build)),
        ] == [
            "Connection()",
            "Column('a')",
            "Follow('k', 'f')",
            "Follow('k', 'f', allow_null=True)",
            f"Expression({build.__qualname__!r})",
        ]


class TestWhatEachRefuses:
    def test_a_column_the_row_hasnt(self):
        assert Column("colour").check(Parcel, kind()) == [
            "k reads its partition from colour, which testapp.Parcel has no column for."
        ]
        assert Column("status").check(Parcel, kind()) == []

    def test_following_what_isnt_a_key(self):
        assert Follow("status", "x").check(Parcel, kind()) == [
            "k follows status to its partition, which isn't a key of testapp.Parcel."
        ]
        assert Follow("colour", "x").check(Parcel, kind()) == [
            "k follows colour to its partition, which isn't a key of testapp.Parcel."
        ]

    def test_following_to_a_column_the_other_row_hasnt(self):
        assert Follow("sender", "colour").check(Parcel, kind()) == [
            "k reads its partition from testapp.EmailUser.colour, which has no column."
        ]

    def test_a_key_that_may_be_empty_unless_meant(self):
        assert Follow("recipient", "username").check(Parcel, kind()) == [
            "k follows recipient to its partition, which may be empty - a notice with no partition reaches "
            "nobody. Say allow_null=True if that's meant."
        ]
        assert Follow("recipient", "username", allow_null=True).check(Parcel, kind()) == []
        assert Follow("sender", "username").check(Parcel, kind()) == []

    @isolate_apps("tests.testapp")
    def test_a_database_cascade_on_a_delete_where_django_has_one(self, monkeypatch):
        def db_cascade(*args):
            raise AssertionError("never run - Postgres does the deleting")

        monkeypatch.setattr(models, "DB_CASCADE", db_cascade, raising=False)
        name = f"Cascaded{uuid.uuid4().hex[:8]}"
        cascaded = type(
            name,
            (models.Model,),
            {
                "__module__": __name__,
                "owner": models.ForeignKey(EmailUser, on_delete=db_cascade, related_name="+"),
                "Meta": type("Meta", (), {"app_label": "testapp"}),
            },
        )
        follow = Follow("owner", "username")

        assert follow.check(cascaded, kind(on=pgtrigger.Delete)) == [
            "k follows owner to its partition on a delete, and the database deletes what it points at first - "
            "use Column, or Django's CASCADE."
        ]
        assert follow.check(cascaded, kind(on=pgtrigger.Update)) == []
        # Django's own CASCADE, as Parcel.sender has, deletes the dependents first - nothing to refuse.
        assert Follow("sender", "username").check(Parcel, kind(on=pgtrigger.Delete)) == []

    def test_no_database_cascade_where_django_hasnt_one(self, monkeypatch):
        monkeypatch.delattr(models, "DB_CASCADE", raising=False)

        assert Follow("sender", "username").check(Parcel, kind(on=pgtrigger.Delete)) == []


class TestTheChecksAboutPartitions:
    def shipped(self, **changes):
        return dataclasses.replace(kinds.get_kind("parcel_shipped"), **changes)

    def test_a_kinds_resolver_is_checked(self, declaring):
        declaring(self.shipped(partition=Column("colour")))

        assert kind_problems() == [
            "parcel_shipped reads its partition from colour, which testapp.Parcel has no column for."
        ]

    def test_the_default_resolver_is_checked_for_each_kind_without_one(self, declaring, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "DEFAULT_PARTITION": Column("colour")}
        flagged = dataclasses.replace(kinds.get_kind("parcel_flagged"), partition=Column("status"))
        declaring(self.shipped(), flagged)

        assert kind_problems() == [
            "parcel_shipped reads its partition from colour, which testapp.Parcel has no column for."
        ]

    def test_a_partition_that_isnt_a_resolver(self, declaring):
        declaring(self.shipped(partition="current_schema()"))

        assert kind_problems() == [
            "parcel_shipped's partition= is a resolver from isik.django.apps.notifications.partitions."
        ]

    def test_a_default_that_isnt_a_resolver(self, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "DEFAULT_PARTITION": "current_schema()"}

        assert setting_problems() == [
            "ISIK_NOTIFICATIONS' DEFAULT_PARTITION is a resolver from isik.django.apps.notifications.partitions."
        ]

    def test_partitions_with_nowhere_to_read_a_kinds_from(self, declaring, settings):
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "PARTITIONS": list,
            "IN_PARTITION": nullcontext,
        }
        declaring(self.shipped(), kinds.get_kind("parcel_routed"))

        assert kind_problems() == [
            "parcel_shipped says nowhere which partition its row belongs to - give it partition=, or set "
            "ISIK_NOTIFICATIONS' DEFAULT_PARTITION."
        ]

    def test_no_partitions_need_no_resolver(self, declaring):
        declaring(self.shipped())

        assert kind_problems() == []


@pytest.mark.django_db
def test_a_notice_stores_the_partition_its_resolver_read(alice):
    Parcel.objects.create(sender=alice, status="routed")

    notice = Notice.objects.get(kind_id="parcel_routed")

    assert notice.subject_partition == alice.username


def test_a_constant_partition():
    from isik.django.apps.notifications.partitions import Constant

    assert Constant("public").resolve(Parcel) == "'public'"
    assert Constant("it's").resolve(Parcel) == "'it''s'"
    assert repr(Constant("public")) == "Constant('public')"
    assert Constant("public").check(Parcel, kind()) == []
