"""enum_name_overrides() and SpectacularAPIView - a schema whose enum names and language don't drift."""

import enum

import pytest
from django.db import models
from django.test import RequestFactory, override_settings
from drf_spectacular.drainage import GENERATOR_STATS, reset_generator_stats
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.plumbing import list_hash
from drf_spectacular.settings import patched_settings
from rest_framework import serializers
from rest_framework.mixins import ListModelMixin
from rest_framework.routers import SimpleRouter
from rest_framework.viewsets import GenericViewSet

from isik.django.apps.common import generated
from isik.django.drf import spectacular
from isik.django.drf.schema_views import SpectacularAPIView
from isik.django.drf.spectacular import _choices_key, _common_name, enum_name_overrides
from tests.testapp.models import Widget


VOTE = [(1, "up"), (-1, "down")]


class Shade(models.TextChoices):
    LIGHT = "light", "Light"
    DARK = "dark", "Dark"


class Size(enum.Enum):
    S = "s"
    M = "m"


def shades():
    return [("light", "Light"), ("dark", "Dark")]


def vote_model(name="PostVotesVote"):
    return next(model for model in generated.generated_models("maker") if model.__name__ == name)


def serializer_for(model, fields):
    meta = type("Meta", (), {"model": model, "fields": fields})
    return type(f"{model.__name__}Serializer", (serializers.ModelSerializer,), {"Meta": meta})


class RatingSerializer(serializers.Serializer):
    value = serializers.ChoiceField(choices=[("a", "A"), ("b", "B")])


def listing(name, model, serializer):
    return type(
        name, (ListModelMixin, GenericViewSet), {"queryset": model.objects.all(), "serializer_class": serializer}
    )


def schema(overrides):
    # Two vote models' `value`, and a project's own `value` of other choices: drf-spectacular can name
    # neither apart by its field or its component.
    votes = [vote_model(name) for name in ("PostVotesVote", "MultiVoteHostUpvotesVote")]
    ratings = type(
        "Ratings",
        (ListModelMixin, GenericViewSet),
        {"queryset": Widget.objects.all(), "serializer_class": RatingSerializer},
    )
    router = SimpleRouter()
    for model in votes:
        router.register(model.__name__.lower(), listing(model.__name__, model, serializer_for(model, ["id", "value"])))
    router.register("ratings", ratings, basename="ratings")
    reset_generator_stats()
    rest_framework = {"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}
    with override_settings(REST_FRAMEWORK=rest_framework), patched_settings({"ENUM_NAME_OVERRIDES": overrides}):
        return SchemaGenerator(patterns=router.urls).get_schema(request=None, public=True)


@pytest.fixture(autouse=True)
def only_the_testapps_votes(monkeypatch):
    # The registry also holds what other tests generated under isolate_apps - names come from every
    # model sharing a choice set, so these tests name theirs.
    votes = [vote_model(name) for name in ("PostVotesVote", "MultiVoteHostUpvotesVote")]
    monkeypatch.setattr(spectacular, "generated_models", lambda: votes)


@pytest.fixture(autouse=True)
def fresh_overrides():
    from drf_spectacular import plumbing

    plumbing._load_enum_name_overrides.cache_clear()
    yield
    plumbing._load_enum_name_overrides.cache_clear()


@pytest.mark.django_db
class TestInASchema:
    def test_without_it_a_projects_value_and_the_votes_collide(self):
        schema({})

        assert any("collision" in warning for warning in GENERATOR_STATS._warn_cache)

    def test_with_it_they_dont(self):
        generated_schema = schema(enum_name_overrides())

        assert not GENERATOR_STATS._warn_cache
        assert not GENERATOR_STATS._error_cache
        assert "VoteValueEnum" in generated_schema["components"]["schemas"]

    def test_a_projects_own_name_for_the_votes_wins(self):
        generated_schema = schema(enum_name_overrides({"BallotEnum": VOTE}))

        assert not GENERATOR_STATS._warn_cache
        assert not GENERATOR_STATS._error_cache
        assert "BallotEnum" in generated_schema["components"]["schemas"]
        assert "VoteValueEnum" not in generated_schema["components"]["schemas"]


class TestTheMapping:
    def test_one_name_per_choice_set_after_what_its_models_share(self):
        overrides = enum_name_overrides()

        assert overrides["VoteValueEnum"] == VOTE
        assert len([name for name in overrides if overrides[name] == VOTE]) == 1

    def test_the_projects_own_are_kept_as_given(self):
        overrides = enum_name_overrides({"ShadeEnum": "tests.django.drf.test_enum_names.Shade"})

        assert overrides["ShadeEnum"] == "tests.django.drf.test_enum_names.Shade"
        assert list(overrides)[-1] == "ShadeEnum"

    def test_a_name_the_project_takes_is_its(self):
        overrides = enum_name_overrides({"VoteValueEnum": shades})

        assert overrides["VoteValueEnum"] is shades
        assert len(overrides) == 1

    def test_without_generated_choice_fields_its_only_the_projects(self, monkeypatch):
        monkeypatch.setattr(spectacular, "generated_models", lambda: [Widget])

        assert dict(enum_name_overrides({"ShadeEnum": shades})) == {"ShadeEnum": shades}

    def test_read_when_used_not_when_made(self, monkeypatch):
        monkeypatch.setattr(spectacular, "generated_models", lambda: [])
        overrides = enum_name_overrides()
        monkeypatch.setattr(spectacular, "generated_models", lambda: [vote_model()])

        assert dict(overrides) == {"PostVotesVoteValueEnum": VOTE}

    def test_a_missing_name_is_a_key_error(self):
        with pytest.raises(KeyError):
            enum_name_overrides()["NothingEnum"]


class TestChoicesKey:
    """The same hash drf-spectacular files the choices under - whatever form a project gives them in."""

    @pytest.mark.parametrize(
        "given",
        [
            [("light", "Light"), ("dark", "Dark")],
            "tests.django.drf.test_enum_names.Shade",
            Shade,
            shades,
            [("", "None"), ("light", "Light"), (None, "Null"), ("dark", "Dark")],
            [("Tones", [("light", "Light"), ("dark", "Dark")])],
        ],
    )
    def test_every_form_of_one_choice_set(self, given):
        assert _choices_key(given) == list_hash([("light", "Light"), ("dark", "Dark")])

    def test_an_enum(self):
        assert _choices_key(Size) == list_hash([("s", "S"), ("m", "M")])

    def test_plain_values(self):
        assert _choices_key(["a", None, "b"]) == list_hash([("a", "a"), ("b", "b")])


class TestCommonName:
    def named(self, *names):
        return [type(name, (), {}) for name in names]

    def test_the_words_they_end_with(self):
        assert _common_name(self.named("PostVotesVote", "MultiVoteHostHelpfulnessVote")) == "Vote"

    def test_several_words(self):
        assert _common_name(self.named("PostCommentVote", "PageCommentVote")) == "CommentVote"

    def test_one_models_whole_name(self):
        assert _common_name(self.named("WidgetEvent")) == "WidgetEvent"

    def test_nothing_in_common_is_the_first_name(self):
        assert _common_name(self.named("WidgetEvent", "PostVote")) == "WidgetEvent"

    def test_a_shorter_name_ends_the_comparison(self):
        assert _common_name(self.named("Vote", "PostVote")) == "Vote"


@pytest.mark.django_db
class TestTheSchemaLanguage:
    def run(self, monkeypatch, query=None):
        """[(the language it was generated in, whether it was handed the request)]."""
        from django.utils import translation
        from drf_spectacular.views import SpectacularAPIView as Base
        from rest_framework.response import Response

        seen = []

        def generate(view, given):
            seen.append((translation.get_language(), given is view.request))
            return Response({})

        monkeypatch.setattr(Base, "_get_schema_response", generate)
        with translation.override("de"):
            SpectacularAPIView.as_view()(RequestFactory().get("/schema/", query or {}))
        return seen

    def test_generated_in_the_pinned_language(self, monkeypatch, settings):
        settings.ISIK_SCHEMA_LANGUAGE = "fr"

        assert self.run(monkeypatch) == [("fr", True)]

    def test_a_lang_query_still_picks_another(self, monkeypatch, settings):
        settings.ISIK_SCHEMA_LANGUAGE = "fr"

        assert self.run(monkeypatch, {"lang": "tr"}) == [("tr", True)]

    def test_without_the_setting_the_requests_own(self, monkeypatch, settings):
        if hasattr(settings, "ISIK_SCHEMA_LANGUAGE"):
            del settings.ISIK_SCHEMA_LANGUAGE

        assert self.run(monkeypatch) == [("de", True)]
