"""ScopedRelatedField - a ScopedForeignKey read by its column, written as an id in the serializer's scope."""

import types
import uuid

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers

from isik.django.drf.serializers import ScopedRelatedField
from tests.testapp.models import Attempt, AttemptResponse, Campaign


pytestmark = pytest.mark.django_db


class ResponseSerializer(serializers.ModelSerializer):
    attempt = ScopedRelatedField(scope="campaign", queryset=Attempt.objects.all())
    follow_up = ScopedRelatedField(scope="campaign", queryset=Attempt.objects.all(), required=False, allow_null=True)

    class Meta:
        model = AttemptResponse
        fields = ["id", "campaign", "attempt", "follow_up", "answer"]


def allowed(request):
    return request.allowed


class DerivedScopeSerializer(serializers.ModelSerializer):
    """A serializer whose scope the server derives - the client never sends it."""

    attempt = ScopedRelatedField(scope="campaign", queryset=Attempt.objects.all(), scopes=allowed)

    class Meta:
        model = AttemptResponse
        fields = ["id", "campaign", "attempt", "answer"]
        read_only_fields = ["campaign"]


def as_member_of(scopes):
    return {"request": types.SimpleNamespace(allowed=scopes)}


@pytest.fixture
def north():
    return Campaign.objects.create(name="north")


@pytest.fixture
def south():
    return Campaign.objects.create(name="south")


@pytest.fixture
def knock(north):
    return Attempt.objects.create(campaign=north, door="12")


class TestReading:
    def test_by_its_column(self, north, knock, django_assert_num_queries):
        response = AttemptResponse.objects.create(campaign=north, attempt=knock, answer="yes")
        response = AttemptResponse.objects.get(pk=response.pk)

        with django_assert_num_queries(0):
            data = ResponseSerializer(response).data

        assert (data["attempt"], data["follow_up"]) == (knock.pk, None)


class TestWriting:
    def test_an_attempt_in_the_scope_sent(self, north, knock):
        serializer = ResponseSerializer(data={"campaign": north.pk, "attempt": str(knock.pk), "answer": "yes"})

        assert serializer.is_valid(), serializer.errors
        response = serializer.save()

        assert (response.attempt_id, response.campaign_id) == (knock.pk, north.pk)

    def test_an_attempt_in_another_scope_reads_as_one_not_there(self, south, knock):
        serializer = ResponseSerializer(data={"campaign": south.pk, "attempt": str(knock.pk), "answer": "yes"})

        assert not serializer.is_valid()
        assert [error.code for error in serializer.errors["attempt"]] == ["does_not_exist"]

    @pytest.mark.parametrize("campaign", ["not a number", None])
    def test_a_scope_that_isnt_one(self, knock, campaign):
        serializer = ResponseSerializer(data={"campaign": campaign, "attempt": str(knock.pk), "answer": "yes"})

        assert not serializer.is_valid()
        assert [error.code for error in serializer.errors["attempt"]] == ["does_not_exist"]

    def test_a_scope_the_server_derives_comes_from_the_attempt(self, north, knock):
        serializer = DerivedScopeSerializer(
            data={"attempt": str(knock.pk), "answer": "yes"}, context=as_member_of([north.pk])
        )

        assert serializer.is_valid(), serializer.errors
        response = serializer.save()

        assert (response.attempt_id, response.campaign_id) == (knock.pk, north.pk)

    def test_a_scope_sent_to_a_serializer_deriving_it_is_ignored(self, south, knock):
        serializer = DerivedScopeSerializer(
            data={"campaign": "junk", "attempt": str(knock.pk), "answer": "yes"},
            context=as_member_of(knock.campaign_id),
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.save().campaign_id == knock.campaign_id

    def test_a_derived_scopes_targets_are_its_querysets(self, knock):
        class Limited(DerivedScopeSerializer):
            attempt = ScopedRelatedField(scope="campaign", queryset=Attempt.objects.none(), scopes=allowed)

        serializer = Limited(
            data={"attempt": str(knock.pk), "answer": "yes"}, context=as_member_of([knock.campaign_id])
        )

        assert not serializer.is_valid()
        assert [error.code for error in serializer.errors["attempt"]] == ["does_not_exist"]

    def test_a_parent_in_a_scope_the_request_may_not_write_to(self, north, south):
        theirs = Attempt.objects.create(campaign=south, door="9")
        serializer = DerivedScopeSerializer(
            data={"attempt": str(theirs.pk), "answer": "yes"}, context=as_member_of([north.pk])
        )

        assert not serializer.is_valid()
        assert [error.code for error in serializer.errors["attempt"]] == ["does_not_exist"]
        assert not AttemptResponse.objects.filter(campaign=south).exists()

    @pytest.mark.parametrize("shape", ["one", "list", "queryset"])
    def test_scopes_as_one_several_or_a_queryset(self, north, knock, shape):
        given = {
            "one": north.pk,
            "list": [north.pk],
            "queryset": Campaign.objects.filter(pk=north.pk),
        }[shape]
        serializer = DerivedScopeSerializer(
            data={"attempt": str(knock.pk), "answer": "yes"}, context=as_member_of(given)
        )

        assert serializer.is_valid(), serializer.errors

    def test_no_request_in_the_context_is_refused(self, knock):
        with pytest.raises(ImproperlyConfigured) as refused:
            DerivedScopeSerializer(data={"attempt": str(knock.pk), "answer": "yes"}).is_valid()

        assert str(refused.value) == (
            "attempt writes only to the scopes a request may write to - give its serializer the request in its context."
        )

    def test_a_scope_derived_with_no_scopes_is_refused(self, knock):
        class Unbounded(DerivedScopeSerializer):
            attempt = ScopedRelatedField(scope="campaign", queryset=Attempt.objects.all())

        with pytest.raises(ImproperlyConfigured) as refused:
            Unbounded(data={"attempt": str(knock.pk), "answer": "yes"}).is_valid()

        assert str(refused.value) == (
            "attempt takes its scope from the target a request names - give it scopes=, the scopes a request may "
            "write to, or any target of any scope would do."
        )

    def test_a_scope_sent_is_held_to_the_requests_scopes(self, north, south, knock):
        class Sent(ResponseSerializer):
            attempt = ScopedRelatedField(scope="campaign", queryset=Attempt.objects.all(), scopes=allowed)

        theirs = Attempt.objects.create(campaign=south, door="9")
        serializer = Sent(
            data={"campaign": south.pk, "attempt": str(theirs.pk), "answer": "yes"}, context=as_member_of([north.pk])
        )

        assert not serializer.is_valid()
        assert [error.code for error in serializer.errors["attempt"]] == ["does_not_exist"]

    def test_an_update_in_the_instances_scope(self, north, knock):
        response = AttemptResponse.objects.create(campaign=north, attempt=knock, answer="yes")
        later = Attempt.objects.create(campaign=north, door="14")

        serializer = ResponseSerializer(response, data={"follow_up": str(later.pk)}, partial=True)

        assert serializer.is_valid(), serializer.errors
        assert serializer.save().follow_up_id == later.pk

    def test_an_update_to_another_scopes_attempt(self, north, south, knock):
        response = AttemptResponse.objects.create(campaign=north, attempt=knock, answer="yes")
        elsewhere = Attempt.objects.create(campaign=south, door="3")

        serializer = ResponseSerializer(response, data={"follow_up": str(elsewhere.pk)}, partial=True)

        assert not serializer.is_valid()
        assert list(serializer.errors) == ["follow_up"]

    def test_an_unknown_id(self, north):
        serializer = ResponseSerializer(data={"campaign": north.pk, "attempt": str(uuid.uuid4()), "answer": "yes"})

        assert not serializer.is_valid()
        assert [error.code for error in serializer.errors["attempt"]] == ["does_not_exist"]

    def test_the_queryset_outside_a_model_serializer(self, knock):
        field = ScopedRelatedField(scope="campaign", queryset=Attempt.objects.all(), scopes=allowed)
        field.bind("attempt", serializers.Serializer(context=as_member_of([knock.campaign_id])))

        assert list(field.get_queryset()) == [knock]


class TestTheSchema:
    def test_described_like_a_primary_key_related_field(self):
        from drf_spectacular.openapi import AutoSchema

        schema = AutoSchema()
        schema.registry = types.SimpleNamespace(register_on_missing=lambda *args: None)
        serializer = ResponseSerializer()

        attempt = schema._map_serializer_field(serializer.fields["attempt"], "request")
        follow_up = schema._map_serializer_field(serializer.fields["follow_up"], "request")

        shape = ("type", "format", "nullable")
        assert {key: attempt.get(key) for key in shape} == {"type": "string", "format": "uuid", "nullable": None}
        assert {key: follow_up.get(key) for key in shape} == {"type": "string", "format": "uuid", "nullable": True}
