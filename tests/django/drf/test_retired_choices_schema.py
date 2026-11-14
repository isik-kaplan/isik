"""A choice field's response schema with the retired values a row may still hold - its request schema without."""

import types

import pytest
from drf_spectacular.settings import patched_settings
from rest_framework import serializers
from rest_framework.mixins import CreateModelMixin, ListModelMixin
from rest_framework.viewsets import GenericViewSet

from isik.django.drf import spectacular
from tests.django.drf.test_spectacular import generate_schema
from tests.testapp.models import Manuscript


pytestmark = pytest.mark.django_db


class ManuscriptSerializer(serializers.ModelSerializer):
    renamed = serializers.ChoiceField(source="stage", choices=[("draft", "Draft")], read_only=True)
    tags = serializers.MultipleChoiceField(choices=[("a", "A")], required=False, write_only=True)
    plain = serializers.ChoiceField(choices=[("a", "A")], required=False, write_only=True)

    class Meta:
        model = Manuscript
        fields = ["id", "stage", "level", "renamed", "tags", "plain"]


class Manuscripts(CreateModelMixin, ListModelMixin, GenericViewSet):
    endpoint = "manuscripts"
    queryset = Manuscript.objects.all()
    serializer_class = ManuscriptSerializer


def components():
    with patched_settings({"COMPONENT_SPLIT_REQUEST": True}):
        return generate_schema((Manuscripts, "manuscript"))["components"]["schemas"]


def test_the_response_reads_a_choice_or_a_retired_value():
    stage = components()["Manuscript"]["properties"]["stage"]

    assert stage == {
        "oneOf": [
            {"type": "string", "enum": ["draft", "review", "published"]},
            {
                "enum": ["legacy"],
                "type": "string",
                "description": "Retired - only a row already holding one has it, and nothing writes it anew.",
            },
        ],
        "x-retired": ["legacy"],
        "description": "* `draft` - Draft\n* `review` - In review\n* `published` - Published",
    }


def test_the_request_takes_the_choices_alone():
    stage = components()["ManuscriptRequest"]["properties"]["stage"]

    assert stage == {"$ref": "#/components/schemas/StageEnum"}


def test_a_field_reading_it_under_another_name():
    assert components()["Manuscript"]["properties"]["renamed"]["x-retired"] == ["legacy"]


def test_a_field_without_retired_values_is_as_drf_spectacular_has_it():
    schemas = components()

    assert "x-retired" not in str(schemas["Manuscript"]["properties"]["level"])
    assert "x-retired" not in str(schemas["ManuscriptRequest"]["properties"]["tags"])
    assert "x-retired" not in str(schemas["ManuscriptRequest"]["properties"]["plain"])


@pytest.mark.parametrize(
    "field",
    [
        serializers.CharField(),
        serializers.MultipleChoiceField(choices=[("legacy", "L")], source="stage"),
        types.SimpleNamespace(),
    ],
    ids=["not-a-choice", "multiple", "not-a-field"],
)
def test_no_retired_values_for_what_isnt_a_choice_field(field):
    assert spectacular._retired_values(field) == ()


def test_no_retired_values_off_a_model_or_its_fields():
    loose = serializers.ChoiceField(choices=[("a", "A")])
    loose.bind("stage", serializers.Serializer())
    dotted = serializers.ChoiceField(choices=[("a", "A")], source="author.stage")
    dotted.bind("stage", ManuscriptSerializer())
    missing = serializers.ChoiceField(choices=[("a", "A")])
    missing.bind("gone", ManuscriptSerializer())
    unruled = serializers.ChoiceField(choices=[("a", "A")])
    unruled.bind("title", ManuscriptSerializer())
    ruled = serializers.ChoiceField(choices=[("a", "A")])
    ruled.bind("stage", ManuscriptSerializer())

    assert [spectacular._retired_values(field) for field in (loose, dotted, missing, unruled, ruled)] == [
        (),
        (),
        (),
        (),
        ("legacy",),
    ]


@pytest.mark.parametrize("bypass", [True, False])
def test_drf_spectacular_maps_the_field_as_asked(monkeypatch, bypass):
    seen = []
    monkeypatch.setattr(
        spectacular._BaseAutoSchema,
        "_map_serializer_field",
        lambda self, field, direction, bypass_extensions=False: seen.append((direction, bypass_extensions)) or {},
    )

    schema = spectacular.AutoSchema.__new__(spectacular.AutoSchema)
    schema._map_serializer_field(serializers.CharField(), "request", bypass_extensions=bypass)

    assert seen == [("request", bypass)]
