import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers
from rest_framework.exceptions import ValidationError as DRFValidationError

from isik.django.drf.serializers.flattened_one_to_one import FlattenedOneToOneMixin
from tests.testapp.models import Widget, WidgetProfile, WidgetSettings


pytestmark = pytest.mark.django_db


class WidgetProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = WidgetProfile
        fields = ["bio"]


class WidgetSerializer(FlattenedOneToOneMixin, serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name"]
        flattened_one_to_one_fields = {"profile": WidgetProfileSerializer}


class TestFlattenedOneToOneMixinRead:
    def test_returns_none_for_the_flattened_fields_when_the_related_row_does_not_exist(self):
        widget = Widget.objects.create(name="bolt")
        assert WidgetSerializer(widget).data == {"id": str(widget.id), "name": "bolt", "bio": None}

    def test_returns_the_related_rows_fields_when_it_exists(self):
        widget = Widget.objects.create(name="bolt")
        WidgetProfile.objects.create(widget=widget, bio="a bolt")
        assert WidgetSerializer(widget).data == {"id": str(widget.id), "name": "bolt", "bio": "a bolt"}

    def test_a_nested_fields_own_positional_constructor_args_survive_reconstruction(self):
        # ChoiceField takes `choices` positionally - dropping the reconstructed field's *_args
        # would silently lose it (TypeError: missing choices, since it has no default).
        class ChoiceProfileSerializer(serializers.ModelSerializer):
            mood = serializers.ChoiceField(["good", "bad"])

            class Meta:
                model = WidgetProfile
                fields = ["bio", "mood"]

        class ChoiceWidgetSerializer(FlattenedOneToOneMixin, serializers.ModelSerializer):
            class Meta:
                model = Widget
                fields = ["id", "name"]
                flattened_one_to_one_fields = {"profile": ChoiceProfileSerializer}

        widget = Widget.objects.create(name="bolt")
        assert ChoiceWidgetSerializer(widget).fields["mood"].choices == {"good": "good", "bad": "bad"}


class TestFlattenedOneToOneMixinWrite:
    def test_create_creates_both_the_parent_and_the_related_row(self):
        serializer = WidgetSerializer(data={"name": "bolt", "bio": "a bolt"})
        serializer.is_valid(raise_exception=True)
        widget = serializer.save()
        assert widget.name == "bolt"
        assert widget.profile.bio == "a bolt"

    def test_create_without_the_flattened_field_does_not_create_a_related_row(self):
        serializer = WidgetSerializer(data={"name": "bolt"})
        serializer.is_valid(raise_exception=True)
        widget = serializer.save()
        assert not WidgetProfile.objects.filter(widget=widget).exists()

    def test_create_rolls_back_the_parent_if_the_related_write_fails(self, monkeypatch):
        def boom(**kwargs):
            raise ValueError("boom")

        monkeypatch.setattr(WidgetProfile.objects, "create", boom)
        serializer = WidgetSerializer(data={"name": "bolt", "bio": "a bolt"})
        serializer.is_valid(raise_exception=True)
        with pytest.raises(ValueError, match="boom"):
            serializer.save()
        assert not Widget.objects.filter(name="bolt").exists()

    def test_update_updates_the_related_row_in_place_without_creating_a_duplicate(self):
        widget = Widget.objects.create(name="bolt")
        profile = WidgetProfile.objects.create(widget=widget, bio="old")
        serializer = WidgetSerializer(widget, data={"name": "bolt", "bio": "new"})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        profile.refresh_from_db()
        assert profile.bio == "new"
        assert WidgetProfile.objects.filter(widget=widget).count() == 1

    def test_update_creates_the_related_row_if_it_did_not_exist_yet(self):
        widget = Widget.objects.create(name="bolt")
        serializer = WidgetSerializer(widget, data={"bio": "new"}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        widget.refresh_from_db()
        assert widget.profile.bio == "new"

    def test_partial_update_omitting_the_flattened_field_leaves_the_related_row_untouched(self):
        widget = Widget.objects.create(name="bolt")
        WidgetProfile.objects.create(widget=widget, bio="untouched")
        serializer = WidgetSerializer(widget, data={"name": "renamed"}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        widget.refresh_from_db()
        assert widget.name == "renamed"
        assert widget.profile.bio == "untouched"


class TestFlattenedOneToOneMixinValidationTranslation:
    def test_a_model_level_clean_error_on_create_becomes_a_drf_validation_error(self):
        # WidgetProfile.clean() rejects bio="banned" - a check DRF's automatic field validation
        # has no visibility into, since it's only reachable via full_clean() inside save().
        serializer = WidgetSerializer(data={"name": "bolt", "bio": "banned"})
        serializer.is_valid(raise_exception=True)
        with pytest.raises(DRFValidationError) as exc_info:
            serializer.save()
        assert exc_info.value.detail == {"bio": ["This bio is not allowed."]}

    def test_a_model_level_clean_error_on_create_still_rolls_back_the_parent(self):
        serializer = WidgetSerializer(data={"name": "bolt", "bio": "banned"})
        serializer.is_valid(raise_exception=True)
        with pytest.raises(DRFValidationError):
            serializer.save()
        assert not Widget.objects.filter(name="bolt").exists()

    def test_a_model_level_clean_error_on_update_becomes_a_drf_validation_error(self):
        widget = Widget.objects.create(name="bolt")
        WidgetProfile.objects.create(widget=widget, bio="fine")
        serializer = WidgetSerializer(widget, data={"bio": "banned"}, partial=True)
        serializer.is_valid(raise_exception=True)
        with pytest.raises(DRFValidationError) as exc_info:
            serializer.save()
        assert exc_info.value.detail == {"bio": ["This bio is not allowed."]}


class TestFlattenedOneToOneMixinDeleteCascade:
    def test_deleting_the_parent_still_cascades_to_the_related_row(self):
        widget = Widget.objects.create(name="bolt")
        WidgetProfile.objects.create(widget=widget, bio="a bolt")
        widget.delete()
        assert not WidgetProfile.objects.filter(bio="a bolt").exists()


class TestFlattenedOneToOneMixinValidation:
    def test_field_name_collision_across_two_flattened_accessors_raises(self):
        class WidgetSettingsSerializer(serializers.ModelSerializer):
            class Meta:
                model = WidgetSettings
                fields = ["bio"]

        with pytest.raises(
            ImproperlyConfigured,
            match=r"flattened field '.+' is declared by both 'profile' and 'settings'\.$",
        ):

            class ConflictingSerializer(FlattenedOneToOneMixin, serializers.ModelSerializer):
                class Meta:
                    model = Widget
                    fields = ["id", "name"]
                    flattened_one_to_one_fields = {
                        "profile": WidgetProfileSerializer,
                        "settings": WidgetSettingsSerializer,
                    }

    def test_a_field_name_that_is_not_a_real_field_raises(self):
        with pytest.raises(ImproperlyConfigured, match="isn't a field on Widget"):

            class BadFieldNameSerializer(FlattenedOneToOneMixin, serializers.ModelSerializer):
                class Meta:
                    model = Widget
                    fields = ["id", "name"]
                    flattened_one_to_one_fields = {"nope": WidgetProfileSerializer}

    def test_a_meta_with_no_model_attribute_is_simply_skipped(self):
        # getattr(own_meta, "model", ...)'s own default has to be an empty/falsy fallback, not
        # missing entirely - a Meta that never sets model at all (as opposed to Meta being absent
        # entirely, already covered elsewhere) must not crash building the class.
        class NoModelMetaSerializer(FlattenedOneToOneMixin, serializers.Serializer):
            class Meta:
                flattened_one_to_one_fields = {"profile": WidgetProfileSerializer}

        assert not hasattr(NoModelMetaSerializer.Meta, "model")

    def test_a_field_name_that_is_not_a_reverse_one_to_one_relation_raises(self):
        with pytest.raises(ImproperlyConfigured, match="isn't a reverse one-to-one relation"):

            class ForwardFieldSerializer(FlattenedOneToOneMixin, serializers.ModelSerializer):
                class Meta:
                    model = Widget
                    fields = ["id", "name"]
                    # "name" is a real field on Widget, but a plain CharField, not a OneToOneRel.
                    flattened_one_to_one_fields = {"name": WidgetProfileSerializer}
