"""One row among a parent's many related ones, flattened into the parent as a field."""

import uuid

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.test.utils import CaptureQueriesContext, isolate_apps
from rest_framework import serializers

from isik.django.drf.serializers import flattened_singleton_related
from isik.django.drf.serializers.flattened_singleton_related import (
    FlattenedSingletonRelatedMixin,
    SingletonRelated,
    singleton_related_problems,
)
from tests.testapp.models import Site, SiteDomain


def site_serializer(on_empty="delete", attribute="domain", name="SiteSerializer"):
    meta = type(
        "Meta",
        (),
        {
            "model": Site,
            "fields": ["id", "name", "primary_domain"],
            "flattened_singleton_related_fields": {
                "primary_domain": SingletonRelated("domains", {"is_primary": True}, attribute, on_empty=on_empty),
            },
        },
    )
    return type(name, (FlattenedSingletonRelatedMixin, serializers.ModelSerializer), {"Meta": meta})


@pytest.fixture
def site(db):
    return Site.objects.create(name="example")


def primary(site):
    return list(site.domains.filter(is_primary=True).values_list("domain", flat=True))


@pytest.mark.django_db
class TestReading:
    def test_shows_the_matching_rows_attribute(self, site):
        SiteDomain.objects.create(site=site, domain="other.example")
        SiteDomain.objects.create(site=site, domain="example.com", is_primary=True)

        assert site_serializer()(site).data["primary_domain"] == "example.com"

    def test_no_row_is_present_and_null(self, site):
        data = site_serializer()(site).data

        assert "primary_domain" in data
        assert data["primary_domain"] is None

    def test_a_null_attribute_is_null(self, site):
        SiteDomain.objects.create(site=site, domain="example.com", is_primary=True)

        assert site_serializer(attribute="note")(site).data["primary_domain"] is None

    def test_left_out_of_the_fields_it_is_absent(self, site):
        narrow = site_serializer()
        narrow.Meta.fields = ["id", "name"]

        assert "primary_domain" not in narrow(site).data

    def test_a_serializer_declaring_none_is_a_plain_one(self, site):
        meta = type("Meta", (), {"model": Site, "fields": ["id", "name"]})
        plain = type(
            "PlainSiteSerializer", (FlattenedSingletonRelatedMixin, serializers.ModelSerializer), {"Meta": meta}
        )

        assert set(plain(site).data) == {"id", "name"}

    def test_a_meta_with_exclude_leaves_it_out(self, site):
        excluding = site_serializer()
        del excluding.Meta.fields
        excluding.Meta.exclude = ["name"]

        assert "primary_domain" not in excluding(site).data

    def test_published_by_all_fields(self, site):
        everything = site_serializer()
        everything.Meta.fields = "__all__"

        assert "primary_domain" in everything(site).data


@pytest.mark.django_db
class TestWriting:
    def write(self, site, value, serializer=None):
        serializer = (serializer or site_serializer())(site, data={"primary_domain": value}, partial=True)
        serializer.is_valid(raise_exception=True)
        return serializer.save()

    def test_creates_the_row_when_there_is_none(self, site):
        self.write(site, "example.com")

        row = site.domains.get()
        assert (row.domain, row.is_primary) == ("example.com", True)

    def test_updates_the_row_in_place_saving_only_the_attribute(self, site):
        existing = SiteDomain.objects.create(site=site, domain="old.example", is_primary=True, note="kept")
        SiteDomain.objects.filter(pk=existing.pk).update(note="changed elsewhere")

        with CaptureQueriesContext(connection) as queries:
            self.write(site, "new.example")

        existing.refresh_from_db()
        assert (existing.domain, existing.note) == ("new.example", "changed elsewhere")
        updates = [query["sql"] for query in queries if query["sql"].startswith('UPDATE "testapp_sitedomain"')]
        assert len(updates) == 1 and '"note"' not in updates[0]

    def test_on_create_too(self, db):
        serializer = site_serializer()(data={"name": "new", "primary_domain": "new.example"})
        serializer.is_valid(raise_exception=True)
        made = serializer.save()

        assert primary(made) == ["new.example"]

    def test_left_out_of_a_create_it_isnt_required(self, db):
        serializer = site_serializer()(data={"name": "new"})

        assert serializer.is_valid(), serializer.errors
        assert not serializer.save().domains.exists()

    def test_leaves_the_other_rows_alone(self, site):
        SiteDomain.objects.create(site=site, domain="other.example")

        self.write(site, "example.com")

        assert sorted(site.domains.values_list("domain", flat=True)) == ["example.com", "other.example"]

    @pytest.mark.parametrize("empty", [None, ""])
    def test_empty_deletes_the_row_by_default(self, site, empty):
        SiteDomain.objects.create(site=site, domain="example.com", is_primary=True)

        self.write(site, empty)

        assert primary(site) == []

    def test_empty_clears_the_attribute_when_asked(self, site):
        SiteDomain.objects.create(site=site, domain="example.com", is_primary=True, note="a note")

        self.write(site, None, site_serializer(on_empty="clear", attribute="note"))

        assert site.domains.get().note is None

    def test_clearing_with_no_row_does_nothing(self, site):
        self.write(site, None, site_serializer(on_empty="clear", attribute="note"))

        assert not site.domains.exists()

    def test_empty_is_refused_when_asked(self, site):
        serializer = site_serializer(on_empty="refuse")(site, data={"primary_domain": ""}, partial=True)

        assert not serializer.is_valid()
        assert serializer.errors == {"primary_domain": ["This field may not be empty."]}

    def test_a_value_is_validated_by_the_models_field(self, site):
        serializer = site_serializer()(site, data={"primary_domain": "x" * 101}, partial=True)

        assert not serializer.is_valid()
        assert "primary_domain" in serializer.errors

    def test_losing_a_race_to_create_it_is_a_400(self, site, monkeypatch):
        from django.db import IntegrityError
        from rest_framework.exceptions import ValidationError

        def collide(self, **kwargs):
            raise IntegrityError("duplicate key value violates unique constraint")

        monkeypatch.setattr(type(site.domains), "create", collide)

        with pytest.raises(ValidationError) as raised:
            self.write(site, "example.com")

        assert raised.value.detail == {
            "primary_domain": ["This was set by another request at the same time - try again."]
        }


class TestDeclaring:
    def test_a_lookup_in_match_is_refused(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            SingletonRelated("domains", {"domain__startswith": "www.", "role__in": ["a"]}, "domain")

        assert str(raised.value) == (
            "SingletonRelated('domains')'s match names domain__startswith, role__in - it takes equality on the related "
            "model's own fields, which is also what a missing row is created with."
        )

    def test_a_computed_value_is_refused(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            SingletonRelated("domains", {"is_primary": models.F("x"), "rank": len, "ok": 1}, "domain")

        assert (
            str(raised.value)
            == "SingletonRelated('domains')'s match computes is_primary, rank - it takes plain values."
        )

    def test_another_on_empty_is_refused(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            SingletonRelated("domains", {"is_primary": True}, "domain", on_empty="ignore")

        assert str(raised.value) == (
            "SingletonRelated('domains')'s on_empty is 'ignore' - it takes delete, clear, refuse."
        )


class TestTheCheck:
    @pytest.fixture(autouse=True)
    def only_these(self, monkeypatch):
        monkeypatch.setattr(flattened_singleton_related, "_declaring", [])

    def test_a_declaration_the_constraint_proves_passes(self):
        site_serializer()

        assert singleton_related_problems() == []

    def test_each_wrong_declaration_is_named(self):
        meta = type(
            "Meta",
            (),
            {
                "model": Site,
                "fields": ["id"],
                "flattened_singleton_related_fields": {
                    "nowhere": SingletonRelated("missing", {"is_primary": True}, "domain"),
                    "unproven": SingletonRelated("domains", {"note": "x"}, "domain"),
                    "back": SingletonRelated("domains", {"site": 1}, "nothing"),
                    "cleared": SingletonRelated("domains", {"is_primary": True}, "domain", on_empty="clear"),
                },
            },
        )
        type("Wrong", (FlattenedSingletonRelatedMixin, serializers.ModelSerializer), {"Meta": meta})

        assert singleton_related_problems() == [
            "Wrong.nowhere: 'missing' isn't a reverse foreign key of testapp.Site.",
            "Wrong.unproven: nothing proves testapp.SiteDomain has one row per testapp.Site matching {'note': 'x'} - "
            "add UniqueConstraint(fields=['site'], condition=Q(**{'note': 'x'})).",
            "Wrong.back: 'site' isn't a concrete field of testapp.SiteDomain other than its key back to testapp.Site.",
            "Wrong.back: 'nothing' isn't a concrete field of testapp.SiteDomain other than its key back to "
            "testapp.Site.",
            "Wrong.back: nothing proves testapp.SiteDomain has one row per testapp.Site matching {'site': 1} - add "
            "UniqueConstraint(fields=['site'], condition=Q(**{'site': 1})).",
            "Wrong.cleared: on_empty='clear' needs 'domain' to be nullable.",
        ]

    @isolate_apps("tests.testapp")
    @pytest.mark.parametrize(
        "condition",
        [
            models.Q(is_primary=True) | models.Q(is_primary=False),
            ~models.Q(is_primary=True),
            models.Q(models.Q(is_primary=True)),
            models.Q(is_primary=True, flag=True),
        ],
    )
    def test_a_constraint_that_isnt_exactly_the_match_proves_nothing(self, condition):
        suffix = uuid.uuid4().hex[:8]
        owner = type(
            f"Owner{suffix}",
            (models.Model,),
            {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})},
        )
        meta = type(
            "Meta",
            (),
            {
                "app_label": "testapp",
                "constraints": [models.UniqueConstraint(fields=["owner"], condition=condition, name=f"c{suffix}")],
            },
        )
        type(
            f"Row{suffix}",
            (models.Model,),
            {
                "__module__": __name__,
                "Meta": meta,
                "owner": models.ForeignKey(owner, models.CASCADE, related_name="rows"),
                "is_primary": models.BooleanField(default=False),
                "flag": models.BooleanField(default=False),
                "value": models.CharField(max_length=10),
            },
        )
        serializer_meta = type(
            "Meta",
            (),
            {
                "model": owner,
                "fields": ["id"],
                "flattened_singleton_related_fields": {"one": SingletonRelated("rows", {"is_primary": True}, "value")},
            },
        )
        type("Unproven", (FlattenedSingletonRelatedMixin, serializers.ModelSerializer), {"Meta": serializer_meta})

        assert [problem for problem in singleton_related_problems() if "nothing proves" in problem]

    @isolate_apps("tests.testapp")
    def test_other_constraints_before_the_one_dont_hide_it(self):
        suffix = uuid.uuid4().hex[:8]
        owner = type(
            f"Owner{suffix}",
            (models.Model,),
            {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})},
        )
        constraints = [
            # A check, not a unique constraint - even with the match as its condition, it proves nothing.
            models.CheckConstraint(condition=models.Q(is_primary=True), name=f"k{suffix}"),
            models.UniqueConstraint(fields=["owner", "value"], name=f"u{suffix}"),
            models.UniqueConstraint(fields=["owner"], condition=models.Q(is_primary=True), name=f"c{suffix}"),
        ]
        type(
            f"Row{suffix}",
            (models.Model,),
            {
                "__module__": __name__,
                "Meta": type("Meta", (), {"app_label": "testapp", "constraints": constraints}),
                "owner": models.ForeignKey(owner, models.CASCADE, related_name="rows"),
                "is_primary": models.BooleanField(default=False),
                "value": models.CharField(max_length=10),
            },
        )
        serializer_meta = type(
            "Meta",
            (),
            {
                "model": owner,
                "fields": ["id"],
                "flattened_singleton_related_fields": {"one": SingletonRelated("rows", {"is_primary": True}, "value")},
            },
        )
        type("Proven", (FlattenedSingletonRelatedMixin, serializers.ModelSerializer), {"Meta": serializer_meta})

        assert singleton_related_problems() == []

    @isolate_apps("tests.testapp")
    def test_a_constraint_over_other_fields_proves_nothing(self):
        suffix = uuid.uuid4().hex[:8]
        owner = type(
            f"Owner{suffix}",
            (models.Model,),
            {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})},
        )
        constraint = models.UniqueConstraint(fields=["value"], condition=models.Q(is_primary=True), name=f"c{suffix}")
        type(
            f"Row{suffix}",
            (models.Model,),
            {
                "__module__": __name__,
                "Meta": type(
                    "Meta",
                    (),
                    {
                        "app_label": "testapp",
                        "constraints": [
                            constraint,
                            models.UniqueConstraint(fields=["owner", "value"], name=f"u{suffix}"),
                        ],
                    },
                ),
                "owner": models.ForeignKey(owner, models.CASCADE, related_name="rows"),
                "is_primary": models.BooleanField(default=False),
                "value": models.CharField(max_length=10),
            },
        )
        serializer_meta = type(
            "Meta",
            (),
            {
                "model": owner,
                "fields": ["id"],
                "flattened_singleton_related_fields": {"one": SingletonRelated("rows", {"is_primary": True}, "value")},
            },
        )
        type("OtherFields", (FlattenedSingletonRelatedMixin, serializers.ModelSerializer), {"Meta": serializer_meta})

        assert len(singleton_related_problems()) == 1

    def test_a_serializer_inheriting_a_declaration_isnt_counted_twice(self):
        parent = site_serializer()
        type("Child", (parent,), {})

        assert flattened_singleton_related._declaring == [parent]
