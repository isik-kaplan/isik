"""The notice trigger, the ledger's protections, and VariantGuard - each against the real database."""

import threading
import uuid

import pghistory
import pgtrigger
import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction
from django.utils import timezone

from isik.django.apps.notifications import triggers
from isik.django.apps.notifications.audience.swapping import get_endpoint_kind_model, get_subscription_model
from isik.django.apps.notifications.models import DeliveryAttempt, Notice, NoticeKindPolicy
from isik.django.apps.notifications.partitions import Column, Connection
from isik.django.apps.notifications.triggers import NoticeTrigger, VariantGuard, literal
from tests.django.apps.notifications import kind_named
from tests.testapp.models import EmailUser, Parcel


pytestmark = pytest.mark.django_db


def notices(kind=None):
    queryset = Notice.objects.order_by("pk")
    return list(queryset.filter(kind__name=kind) if kind else queryset)


class TestTheNoticeTrigger:
    def test_a_change_meeting_the_condition_writes_one(self, parcel, alice, bob):
        parcel.status = "shipped"
        parcel.save()

        [notice] = notices("parcel_shipped")
        assert notice.variant is None
        assert notice.subject_label == "testapp.Parcel"
        assert notice.subject_id == str(parcel.pk)
        assert notice.subject_partition is None
        assert notice.snapshot == {"status": "shipped", "recipient": str(bob.pk), "sender": str(alice.pk)}
        assert notice.previous == {"status": "packed", "recipient": str(bob.pk), "sender": str(alice.pk)}
        assert notice.fanned_out_at is None
        assert notice.occurred_at is not None

    def test_a_change_not_meeting_it_writes_none(self, parcel):
        parcel.tracking_secret = "s3cret"
        parcel.save()
        parcel.status = "shipped"
        parcel.save()
        parcel.status = "shipped"
        parcel.save()

        assert len(notices("parcel_shipped")) == 1

    def test_only_the_snapshot_columns_reach_it(self, parcel):
        Parcel.objects.filter(pk=parcel.pk).update(status="shipped", tracking_secret="s3cret")

        [notice] = notices("parcel_shipped")
        assert "tracking_secret" not in notice.snapshot
        assert "s3cret" not in str(notice.snapshot) + str(notice.previous)

    def test_a_raw_sql_write_writes_it_too(self, parcel):
        # The property the design rests on: the database writes the notice, so nothing can skip it.
        with connection.cursor() as cursor:
            cursor.execute("UPDATE testapp_parcel SET status = 'shipped' WHERE id = %s", [parcel.pk])

        assert len(notices("parcel_shipped")) == 1

    def test_one_notice_per_variant_whose_condition_holds(self, alice):
        lost = Parcel.objects.create(sender=alice, status="lost")
        Parcel.objects.filter(pk=lost.pk).update(status="damaged")

        assert [(notice.variant, notice.snapshot["status"]) for notice in notices("parcel_flagged")] == [
            ("lost", "lost"),
            ("damaged", "damaged"),
        ]

    def test_a_kind_without_previous_keeps_none(self, alice):
        lost = Parcel.objects.create(sender=alice)
        Parcel.objects.filter(pk=lost.pk).update(status="lost")

        [notice] = notices("parcel_flagged")
        assert notice.previous is None

    def test_a_kind_with_no_policy_row_yet_writes_nothing(self, parcel):
        # A data migration, before the mirror wrote the kind's row: nothing happened worth telling.
        with pgtrigger.ignore("isik_notifications.NoticeKindPolicy:protect_policy_delete"):
            NoticeKindPolicy.objects.filter(name="parcel_shipped").delete()

        parcel.status = "shipped"
        parcel.save()

        assert notices("parcel_shipped") == []

    def test_before_the_ledger_exists_a_write_goes_through(self, parcel):
        # A migration writing a watched row before the ledger's own has run.
        with connection.cursor() as cursor:
            cursor.execute('ALTER TABLE "isik_notice_kind_policy" RENAME TO "not_yet"')
            try:
                parcel.status = "shipped"
                parcel.save()
            finally:
                cursor.execute('ALTER TABLE "not_yet" RENAME TO "isik_notice_kind_policy"')

        assert notices("parcel_shipped") == []

    def test_a_retired_kind_writes_nothing(self, parcel):
        NoticeKindPolicy.objects.filter(name="parcel_shipped").update(retired_at=timezone.now())

        parcel.status = "shipped"
        parcel.save()

        assert notices("parcel_shipped") == []

    def test_it_carries_the_history_context(self, parcel):
        with pghistory.context(why="testing"):
            parcel.status = "shipped"
            parcel.save()

        [notice] = notices("parcel_shipped")
        assert notice.pgh_context.metadata == {"why": "testing"}

    def test_outside_a_context_it_carries_none(self, parcel):
        parcel.status = "shipped"
        parcel.save()

        assert notices("parcel_shipped")[0].pgh_context is None

    def test_its_subject_is_the_old_row_on_a_delete(self, parcel):
        kind = NoticeTrigger(kind=type("K", (), {"trigger_name": "t", "on": pgtrigger.Delete, "condition": None})())

        assert kind._value(Parcel, "status") == ('CASE WHEN TG_OP = \'DELETE\' THEN OLD."status" ELSE NEW."status" END')


class TestThePartitionItStores:
    def kind(self, partition):
        from isik.django.apps.notifications.kinds import Kind

        return Kind(name="k", audience=None, policy="mandatory", model=Parcel, on=pgtrigger.Update, partition=partition)

    def test_none_without_a_resolver(self):
        assert NoticeTrigger(kind=self.kind(None))._partition(Parcel) == "NULL"

    def test_the_setting_by_default(self, settings):
        settings.ISIK_NOTIFICATIONS = {"DEFAULT_PARTITION": Connection()}

        assert NoticeTrigger(kind=self.kind(None))._partition(Parcel) == "(current_schema())::text"

    def test_the_kinds_own_over_the_setting(self, settings):
        settings.ISIK_NOTIFICATIONS = {"DEFAULT_PARTITION": Connection()}

        assert NoticeTrigger(kind=self.kind(Column("status")))._partition(Parcel) == (
            '(CASE WHEN TG_OP = \'DELETE\' THEN OLD."status" ELSE NEW."status" END)::text'
        )


def test_literal_doubles_quotes():
    assert literal("it's") == "'it''s'"


class TestTheNoticeIsFrozen:
    @pytest.fixture
    def notice(self, parcel):
        parcel.status = "shipped"
        parcel.save()
        return notices("parcel_shipped")[0]

    def test_its_columns_cant_change(self, notice):
        with pytest.raises(ProgrammingError, match="protect_notice_update"):
            Notice.objects.filter(pk=notice.pk).update(variant="x", fanned_out_at=timezone.now())

    def test_fanned_out_at_is_stamped_once(self, notice):
        now = timezone.now()
        Notice.objects.filter(pk=notice.pk).update(fanned_out_at=now)

        with transaction.atomic(), pytest.raises(ProgrammingError, match="fanned_out_once"):
            Notice.objects.filter(pk=notice.pk).update(fanned_out_at=now)
        with transaction.atomic(), pytest.raises(ProgrammingError, match="fanned_out_once"):
            Notice.objects.filter(pk=notice.pk).update(fanned_out_at=None)

    def test_an_update_that_doesnt_stamp_it_is_refused(self, notice):
        with pytest.raises(ProgrammingError, match="fanned_out_once"):
            Notice.objects.filter(pk=notice.pk).update(fanned_out_at=None)

    def test_it_cant_be_deleted(self, notice):
        with pytest.raises(ProgrammingError, match="protect_notice_delete"):
            Notice.objects.filter(pk=notice.pk).delete()


@pytest.mark.parametrize("table", ["isik_notice", "isik_delivery_attempt"])
def test_a_ledger_table_cant_be_truncated(table):
    with pytest.raises(ProgrammingError, match=f"Cannot truncate {table}"), connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE {table} CASCADE")


class TestThePolicyRow:
    def test_its_kind_cant_change(self):
        with pytest.raises(ProgrammingError, match="protect_kind"):
            NoticeKindPolicy.objects.filter(name="parcel_shipped").update(name="renamed")

    def test_it_cant_be_deleted(self):
        with pytest.raises(ProgrammingError, match="protect_policy_delete"):
            NoticeKindPolicy.objects.filter(name="parcel_reminder").delete()


class TestAttemptsAreAppendOnly:
    @pytest.fixture
    def attempt(self, parcel):
        from isik.django.apps.notifications.models import Delivery

        parcel.status = "shipped"
        parcel.save()
        delivery = Delivery.objects.create(notice=notices()[0], channel="inbox", target_key="user:1")
        return DeliveryAttempt.objects.create(
            delivery=delivery,
            attempt_no=1,
            succeeded=False,
            started_at=timezone.now(),
            duration_ms=1,
            triggered_by="send",
        )

    def test_one_cant_change(self, attempt):
        with pytest.raises(ProgrammingError, match="append_only"):
            DeliveryAttempt.objects.filter(pk=attempt.pk).update(error="rewritten")

    def test_one_cant_be_deleted(self, attempt):
        with pytest.raises(ProgrammingError, match="append_only"):
            DeliveryAttempt.objects.filter(pk=attempt.pk).delete()


class TestTheVariantGuard:
    def subscribe(self, user, variant, kind="parcel_flagged"):
        return get_subscription_model().objects.create(user=user, kind=kind_named(kind), variant=variant)

    def test_a_declared_variant_or_none_is_taken(self, alice, bob):
        self.subscribe(alice, "lost")
        self.subscribe(alice, "damaged")
        self.subscribe(bob, None)

    @pytest.mark.parametrize("variant", [None, "lost"])
    def test_a_kind_that_doesnt_exist_is_refused(self, alice, variant):
        # Before the foreign key, which is deferred - and which a key reaching across schemas doesn't have.
        with pytest.raises(IntegrityError, match="no kind is called that"):
            get_subscription_model().objects.create(user=alice, kind_id=uuid.uuid4(), variant=variant)

    def test_an_undeclared_variant_is_refused(self, alice):
        with pytest.raises(IntegrityError, match="declares no such variant"):
            self.subscribe(alice, "stolen")

    def test_a_kind_declaring_none_refuses_every_variant(self, alice):
        with pytest.raises(IntegrityError, match="declares no such variant"):
            self.subscribe(alice, "lost", kind="parcel_shipped")

    def test_no_variant_beside_some_is_refused(self, alice):
        self.subscribe(alice, "lost")

        with pytest.raises(IntegrityError, match="cannot sit beside"):
            self.subscribe(alice, None)

    def test_some_variant_beside_none_is_refused(self, alice):
        self.subscribe(alice, None)

        with pytest.raises(IntegrityError, match="cannot sit beside one naming a variant for the same user"):
            self.subscribe(alice, "lost")

    def test_an_update_into_it_is_refused(self, alice):
        self.subscribe(alice, "lost")
        other = self.subscribe(alice, "damaged")

        with pytest.raises(IntegrityError, match="cannot sit beside"):
            get_subscription_model().objects.filter(pk=other.pk).update(variant=None)

    def test_a_row_is_compared_with_others_only(self, alice):
        subscription = self.subscribe(alice, None)

        subscription.enabled = False
        subscription.save()

    def test_another_owners_rows_dont_count(self, alice, bob):
        self.subscribe(alice, "lost")

        self.subscribe(bob, None)

    def test_the_same_row_twice_is_refused_by_the_constraint(self, alice):
        self.subscribe(alice, None)

        with pytest.raises(IntegrityError, match="subscription_once"):
            self.subscribe(alice, None)

    def test_endpoint_kinds_are_guarded_by_their_endpoint(self):
        from isik.django.apps.notifications.audience.swapping import get_endpoint_model

        endpoint = get_endpoint_model().objects.create(url="https://example.com/hook", secret="s")
        get_endpoint_kind_model().objects.create(endpoint=endpoint, kind=kind_named("parcel_flagged"), variant="lost")

        with pytest.raises(IntegrityError, match="for the same endpoint"):
            get_endpoint_kind_model().objects.create(endpoint=endpoint, kind=kind_named("parcel_flagged"))

    def test_inbox_items_are_held_to_declared_variants_only(self, alice):
        import uuid

        from isik.django.apps.notifications.audience.swapping import get_inbox_item_model

        items = get_inbox_item_model().objects
        items.create(user=alice, kind=kind_named("parcel_flagged"), variant="lost", delivery_id=uuid.uuid4())
        items.create(user=alice, kind=kind_named("parcel_flagged"), delivery_id=uuid.uuid4())
        with pytest.raises(IntegrityError, match="declares no such variant"):
            items.create(user=alice, kind=kind_named("parcel_flagged"), variant="stolen", delivery_id=uuid.uuid4())

    def test_only_an_owned_guard_locks(self):
        assert "pg_advisory_xact_lock" not in VariantGuard(name="g").get_func(get_subscription_model())
        assert "pg_advisory_xact_lock" in VariantGuard(name="g", owner="user").get_func(get_subscription_model())


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_writers_cant_both_pass_the_guard():
    alice = EmailUser.objects.create(username="alice", email="alice@example.com")
    first_in = threading.Event()
    outcomes = []

    def write(variant, wait):
        try:
            with transaction.atomic():
                get_subscription_model().objects.create(user=alice, kind=kind_named("parcel_flagged"), variant=variant)
                first_in.set()
                if wait:
                    # Hold the lock until the other writer is blocked on it.
                    threading.Event().wait(0.5)
            outcomes.append(variant)
        except IntegrityError:
            outcomes.append("refused")
        finally:
            connection.close()

    holder = threading.Thread(target=write, args=("lost", True))
    holder.start()
    first_in.wait(5)
    second = threading.Thread(target=write, args=(None, False))
    second.start()
    holder.join(10)
    second.join(10)

    assert outcomes == ["lost", "refused"]


# Nothing is written - or planned - until the policy table is there.
GUARD = "IF to_regclass('\"isik_notice_kind_policy\"') IS NOT NULL THEN "


class TestTheCompiledSql:
    """The SQL each trigger compiles to - read off the model, and the same as what the migrations installed."""

    @pytest.mark.parametrize(
        ("model", "name"),
        [
            (Parcel, "notify_parcel_shipped"),
            (Parcel, "notify_parcel_flagged"),
            (get_subscription_model(), "guard_variant"),
            (get_endpoint_kind_model(), "guard_variant"),
        ],
    )
    def test_each_is_installed_as_compiled(self, model, name):
        [trigger] = [trigger for trigger in model._meta.triggers if trigger.name == name]

        assert trigger.get_installation_status(model)[0] == "INSTALLED"

    def test_a_kind_without_variants_writes_one_notice(self):
        [trigger] = [trigger for trigger in Parcel._meta.triggers if trigger.name == "notify_parcel_shipped"]

        func = trigger.get_func(Parcel)

        assert func.count("INSERT INTO") == 1
        assert func.startswith(f'{GUARD}INSERT INTO "isik_notice" ("kind_id", "variant", "subject_label", ')
        assert """SELECT "isik_notice_kind_policy"."id", NULL, 'testapp.Parcel', """ in func
        assert "CASE WHEN TG_OP = 'UPDATE' THEN jsonb_build_object('status', OLD.\"status\", " in func
        assert func.endswith(
            '_pgh_attach_context() FROM "isik_notice_kind_policy" '
            'WHERE "name" = \'parcel_shipped\' AND "retired_at" IS NULL; END IF; RETURN NULL;'
        )

    def test_a_kind_with_variants_writes_one_per_condition_holding(self):
        [trigger] = [trigger for trigger in Parcel._meta.triggers if trigger.name == "notify_parcel_flagged"]

        func = trigger.get_func(Parcel)

        assert func.startswith(f"{GUARD}IF NEW.\"status\" = 'lost' THEN INSERT INTO ")
        assert "END IF; IF NEW.\"status\" = 'damaged' THEN INSERT INTO " in func
        assert """SELECT "isik_notice_kind_policy"."id", 'damaged', """ in func
        assert f", NULL, {triggers.actor()}, {triggers.context()}, _pgh_attach_context()" in func

    def test_a_variant_without_a_condition_is_always_written(self):
        from isik.django.apps.notifications.kinds import Kind

        kind = Kind(
            name="k", audience=None, policy="mandatory", model=Parcel, on=pgtrigger.Update, variants={"v": None}
        )

        assert NoticeTrigger(kind=kind).get_func(Parcel).startswith(f"{GUARD}IF TRUE THEN INSERT INTO ")

    def test_the_guard_reads_its_columns_off_the_model(self):
        func = VariantGuard(name="g", owner="user").get_func(get_subscription_model())

        lookup = 'SELECT 1 FROM "isik_notice_kind_policy" WHERE "id" = NEW."kind_id"'
        assert func.startswith(
            f"IF NOT EXISTS ({lookup}) THEN RAISE EXCEPTION 'isik_notifications_audience.Subscription: no kind is "
            "called that.' USING ERRCODE = 'check_violation'; END IF; "
            f'IF NEW."variant" IS NOT NULL AND NOT EXISTS ({lookup} AND "variants" @> '
            'jsonb_build_array(NEW."variant")) THEN RAISE EXCEPTION '
        )
        assert (
            "PERFORM pg_advisory_xact_lock(hashtext('isik_subscription'), "
            'hashtext(NEW."user_id"::text || \':\' || NEW."kind_id"::text)); '
        ) in func
        assert 'AND "id" IS DISTINCT FROM NEW."id" AND ("variant" IS NULL) <> (NEW."variant" IS NULL)) THEN' in func
        assert "USING ERRCODE = 'check_violation'; END IF; RETURN NEW;" in func
