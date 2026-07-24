import pytest
from rest_framework import serializers

from isik.django.drf.serializers.meta_combining import MetaCombiningMixin


class TestMetaCombiningMixin:
    def test_a_serializer_with_no_meta_at_all_is_left_alone(self):
        class NoMeta(MetaCombiningMixin, serializers.Serializer):
            meta_fields_to_combine = ["relational_fields"]

            class _Meta:
                relational_fields = {"a": 1}

        assert not hasattr(NoMeta, "Meta")

    def test_dict_attributes_are_merged_with_the_subclass_winning_conflicts(self):
        class Base(MetaCombiningMixin, serializers.Serializer):
            meta_fields_to_combine = ["relational_fields"]

            class _Meta:
                relational_fields = {"owner": "owner-default"}

        class Child(Base):
            class Meta:
                relational_fields = {"owner": "owner-override", "tags": "tags-value"}

        assert Child.Meta.relational_fields == {"owner": "owner-override", "tags": "tags-value"}

    def test_list_attributes_are_concatenated_base_first(self):
        class Base(MetaCombiningMixin, serializers.Serializer):
            meta_fields_to_combine = ["fields"]

            class _Meta:
                fields = ["id"]

        class Child(Base):
            class Meta:
                fields = ["name"]

        assert Child.Meta.fields == ["id", "name"]

    def test_a_subclass_that_does_not_set_the_attribute_still_gets_the_base_value(self):
        class Base(MetaCombiningMixin, serializers.Serializer):
            meta_fields_to_combine = ["relational_fields"]

            class _Meta:
                relational_fields = {"owner": "owner-default"}

        class Child(Base):
            class Meta:
                pass

        assert Child.Meta.relational_fields == {"owner": "owner-default"}

    def test_merging_does_not_mutate_the_shared_base_value_across_subclasses(self):
        class Base(MetaCombiningMixin, serializers.Serializer):
            meta_fields_to_combine = ["relational_fields"]

            class _Meta:
                relational_fields = {"owner": "owner-default"}

        class ChildA(Base):
            class Meta:
                relational_fields = {"a": 1}

        class ChildB(Base):
            class Meta:
                relational_fields = {"b": 2}

        assert ChildA.Meta.relational_fields == {"owner": "owner-default", "a": 1}
        assert ChildB.Meta.relational_fields == {"owner": "owner-default", "b": 2}
        assert Base._Meta.relational_fields == {"owner": "owner-default"}

    def test_meta_fields_to_combine_set_but_no_base_meta_defined_at_all_is_a_no_op(self):
        class Base(MetaCombiningMixin, serializers.Serializer):
            meta_fields_to_combine = ["relational_fields"]
            # deliberately no _Meta at all

        class Child(Base):
            class Meta:
                relational_fields = {"owner": "owner-value"}

        assert Child.Meta.relational_fields == {"owner": "owner-value"}

    def test_a_grandchild_with_no_meta_of_its_own_does_not_mutate_the_parents_already_combined_meta(self):
        class Base(MetaCombiningMixin, serializers.Serializer):
            meta_fields_to_combine = ["fields"]

            class _Meta:
                fields = ["id"]

        class Child(Base):
            class Meta:
                fields = ["name"]

        class GrandChild(Child):
            pass

        assert GrandChild.Meta.fields == ["id", "name"]
        assert Child.Meta.fields == ["id", "name"]

    def test_an_unsupported_attribute_type_raises_type_error(self):
        with pytest.raises(TypeError, match="only supports list or dict"):

            class Base(MetaCombiningMixin, serializers.Serializer):
                meta_fields_to_combine = ["count"]

                class _Meta:
                    count = 1

            class Child(Base):
                class Meta:
                    count = 2
