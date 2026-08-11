import pytest
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps

from isik.django.apps.common import _model_makers


pytestmark = pytest.mark.django_db


# _reset_claimed_related_names in tests/conftest.py resets this globally, for every test.


class TestResolveBaseModel:
    def test_explicit_base_model_wins(self):
        class ExplicitBase(models.Model):
            class Meta:
                app_label = "testapp"
                abstract = True

        assert _model_makers.resolve_base_model(ExplicitBase, "MAKER_DOES_NOT_EXIST") is ExplicitBase

    def test_falls_back_to_the_setting_when_no_explicit_base_is_given(self):
        with override_settings(MAKER_TEST_BASE_MODEL="isik.django.apps.common.db.models.BaseModel"):
            from isik.django.apps.common.db.models import BaseModel

            assert _model_makers.resolve_base_model(None, "MAKER_TEST_BASE_MODEL") is BaseModel

    def test_falls_back_to_the_default_base_when_neither_is_set(self):
        assert _model_makers.resolve_base_model(None, "MAKER_DOES_NOT_EXIST") is _model_makers.DefaultMakerBase

    def test_raises_if_the_resolved_base_is_not_abstract(self):
        from tests.testapp.models import Widget

        with pytest.raises(TypeError, match="abstract"):
            _model_makers.resolve_base_model(Widget, "MAKER_DOES_NOT_EXIST")


class TestClaimRelatedName:
    def test_first_claim_succeeds(self):
        _model_makers.claim_related_name("some.Model", "votes", "SomeVote")

    def test_reclaiming_under_the_same_owner_is_a_no_op(self):
        _model_makers.claim_related_name("some.Model", "votes", "SomeVote")
        _model_makers.claim_related_name("some.Model", "votes", "SomeVote")

    def test_a_different_owner_claiming_the_same_name_raises(self):
        _model_makers.claim_related_name("some.Model", "votes", "SomeVote")
        with pytest.raises(ValueError, match="already claimed"):
            _model_makers.claim_related_name("some.Model", "votes", "OtherVote")

    def test_different_related_names_on_the_same_model_dont_clash(self):
        _model_makers.claim_related_name("some.Model", "votes", "SomeVote")
        _model_makers.claim_related_name("some.Model", "bookmarks", "SomeBookmark")

    def test_the_same_related_name_on_different_models_doesnt_clash(self):
        _model_makers.claim_related_name("some.Model", "votes", "SomeVote")
        _model_makers.claim_related_name("other.Model", "votes", "OtherVote")

    @isolate_apps("tests.testapp")
    def test_a_real_model_class_and_its_dotted_string_equivalent_are_treated_as_the_same_key(self):
        class Thing(models.Model):
            class Meta:
                app_label = "testapp"

        _model_makers.claim_related_name(Thing, "votes", "ThingVote")
        with pytest.raises(ValueError, match="already claimed"):
            _model_makers.claim_related_name("testapp.Thing", "votes", "OtherVote")


class _Marker:
    """Stand-in for a maker's own config class (e.g. `_VotesField`), used to test `resolve_field`
    without depending on any concrete maker."""


class _Descriptor:
    """Stand-in for a real Django descriptor - a plain object that supports attribute
    assignment, unlike a bare `object()`."""


class TestExposeAndResolveField:
    @isolate_apps("tests.testapp")
    def test_expose_stashes_model_and_config_and_resolve_field_finds_it(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

        class HostThing(models.Model):
            class Meta:
                app_label = "testapp"

        config = _Marker()
        descriptor = _Descriptor()
        exposed = _model_makers.expose(Host, "thing", descriptor, generated_model=HostThing, config=config)

        assert exposed is descriptor
        assert Host.thing.model is HostThing
        assert Host.thing.config is config
        assert _model_makers.resolve_field(Host(), None, _Marker, "thingable") is exposed
        assert _model_makers.resolve_field(Host, None, _Marker, "thingable") is exposed

    @isolate_apps("tests.testapp")
    def test_resolve_field_raises_when_nothing_is_attached(self):
        class Unmarked(models.Model):
            class Meta:
                app_label = "testapp"

        with pytest.raises(TypeError, match="not thingable"):
            _model_makers.resolve_field(Unmarked(), None, _Marker, "thingable")

    @isolate_apps("tests.testapp")
    def test_resolve_field_raises_when_more_than_one_is_attached_and_none_given_explicitly(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

        descriptor_a = _Descriptor()
        descriptor_b = _Descriptor()
        _model_makers.expose(Host, "a", descriptor_a, generated_model=None, config=_Marker())
        _model_makers.expose(Host, "b", descriptor_b, generated_model=None, config=_Marker())

        with pytest.raises(TypeError, match="multiple thingable fields"):
            _model_makers.resolve_field(Host(), None, _Marker, "thingable")

    @isolate_apps("tests.testapp")
    def test_resolve_field_does_not_double_count_a_name_a_subclass_shadows(self):
        # `seen` has to actually track the real attr_name - if it always recorded the same stand-in
        # value instead, the base class's own same-named attr would never register as "already
        # seen", and a field the subclass overrides would be counted twice (once per class in the
        # mro), wrongly tripping the "multiple X fields" ambiguity error below.
        class Base(models.Model):
            class Meta:
                app_label = "testapp"
                abstract = True

            thing = _Descriptor()

        Base.thing.config = _Marker()

        class Host(Base):
            class Meta:
                app_label = "testapp"

        overriding_descriptor = _Descriptor()
        _model_makers.expose(Host, "thing", overriding_descriptor, generated_model=None, config=_Marker())

        assert _model_makers.resolve_field(Host(), None, _Marker, "thingable") is overriding_descriptor

    @isolate_apps("tests.testapp")
    def test_resolve_field_keeps_scanning_a_classs_own_attrs_past_one_shadowed_by_a_subclass(self):
        # An attr name already in `seen` (because a subclass shadows it) has to be skipped with
        # `continue`, not `break` - the real field lives right after it in Base's own __dict__
        # order, and a `break` there would stop scanning Base entirely before ever reaching it.
        class Base(models.Model):
            class Meta:
                app_label = "testapp"
                abstract = True

            shadowed = "base value"

        descriptor = _Descriptor()
        config = _Marker()

        class Host(Base):
            class Meta:
                app_label = "testapp"

            shadowed = "host value"

        _model_makers.expose(Base, "real_field", descriptor, generated_model=None, config=config)

        assert _model_makers.resolve_field(Host(), None, _Marker, "thingable") is descriptor

    @isolate_apps("tests.testapp")
    def test_resolve_field_uses_the_explicit_field_without_scanning(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

        explicit = object()
        assert _model_makers.resolve_field(Host(), explicit, _Marker, "thingable") is explicit

    @isolate_apps("tests.testapp")
    def test_expose_via_reverse_accessor_resolves_once_the_host_is_registered(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

        class HostThing(models.Model):
            target = models.ForeignKey(Host, on_delete=models.CASCADE, related_name="things")

            class Meta:
                app_label = "testapp"

        config = _Marker()
        _model_makers.expose_via_reverse_accessor(Host, "things", "things", generated_model=HostThing, config=config)

        assert Host.things.model is HostThing
        assert Host.things.config is config


class TestBuildModel:
    @isolate_apps("tests.testapp")
    def test_extra_fields_are_wired_up_via_contribute_to_class_like_a_real_field(self):
        class Host(models.Model):
            class Meta:
                app_label = "testapp"

        class _StampingField:
            def contribute_to_class(self, cls, name):
                cls._was_stamped = True

        generated = _model_makers.build_model(
            "HostThing",
            Host,
            fields={"marker": _StampingField()},
            base_model=_model_makers.DefaultMakerBase,
        )
        assert generated._was_stamped is True
        assert generated._meta.app_label == "testapp"
