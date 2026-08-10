import pytest
from django.db import models
from django.db.models.deletion import ProtectedError
from django.test.utils import isolate_apps
from rest_framework import serializers, status
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import ModelViewSet

from isik.django.drf.viewsets.protected_destroy import ProtectedDestroyMixin
from tests.testapp.models import Comment, Tag, Widget, WidgetTag


pytestmark = pytest.mark.django_db


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]


class WidgetViewSet(ProtectedDestroyMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer


@pytest.fixture
def destroy_view():
    return WidgetViewSet.as_view({"delete": "destroy"})


class TestProtectedDestroyMixin:
    def test_forwards_its_exact_arguments_to_super_destroy(self):
        # DestroyModelMixin.destroy() itself doesn't use request/args/kwargs in its body (it
        # reads self.kwargs instead), so a real ModelViewSet base can't distinguish a dropped or
        # swapped argument here - a fake base that records what it actually received can.
        received = {}

        class RecordingBase:
            def destroy(self, request, *args, **kwargs):
                received["request"] = request
                received["args"] = args
                received["kwargs"] = kwargs

        class RecordingViewSet(ProtectedDestroyMixin, RecordingBase):
            pass

        sentinel_request = object()
        RecordingViewSet().destroy(sentinel_request, "extra", pk="123")

        assert received == {"request": sentinel_request, "args": ("extra",), "kwargs": {"pk": "123"}}

    def test_deletes_normally_when_nothing_protects_it(self, destroy_view):
        widget = Widget.objects.create(name="bolt", count=1)
        request = APIRequestFactory().delete(f"/widgets/{widget.pk}/")
        response = destroy_view(request, pk=widget.pk)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Widget.objects.filter(pk=widget.pk).exists()

    def test_reports_the_model_and_field_blocking_the_delete(self, destroy_view):
        widget = Widget.objects.create(name="bolt", count=1)
        Comment.objects.create(body="hi", widget=widget)
        request = APIRequestFactory().delete(f"/widgets/{widget.pk}/")
        response = destroy_view(request, pk=widget.pk)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"protected_by": [{"model": "Comment", "field": "widget"}]}
        assert Widget.objects.filter(pk=widget.pk).exists()

    def test_groups_by_model_and_field_instead_of_listing_every_object(self, destroy_view):
        widget = Widget.objects.create(name="bolt", count=1)
        Comment.objects.create(body="one", widget=widget)
        Comment.objects.create(body="two", widget=widget)
        request = APIRequestFactory().delete(f"/widgets/{widget.pk}/")
        response = destroy_view(request, pk=widget.pk)
        assert response.data == {"protected_by": [{"model": "Comment", "field": "widget"}]}

    def test_unrelated_errors_are_not_swallowed_into_a_protected_by_response(self, destroy_view):
        request = APIRequestFactory().delete("/widgets/does-not-exist/")
        response = destroy_view(request, pk="00000000-0000-0000-0000-000000000000")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_field_comes_back_as_none_when_no_concrete_relation_resolves_to_the_instance(
        self, destroy_view, monkeypatch
    ):
        # Tag has no FK (or any other concrete relation) pointing at Widget at all, so
        # _protecting_field_name can't find a match - it should report field: None instead of
        # crashing. Real ProtectedErrors from this schema never actually surface a case like this
        # (every real PROTECT relation here does resolve to a field), so the delete is faked via
        # monkeypatching rather than a genuine cascade.
        widget = Widget.objects.create(name="bolt", count=1)
        tag = Tag.objects.create(label="unrelated")

        def fake_delete(*args, **kwargs):
            raise ProtectedError("protected", {tag})

        monkeypatch.setattr(Widget, "delete", fake_delete)
        request = APIRequestFactory().delete(f"/widgets/{widget.pk}/")
        response = destroy_view(request, pk=widget.pk)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"protected_by": [{"model": "Tag", "field": None}]}

    def test_field_comes_back_as_none_when_a_concrete_relation_points_elsewhere_or_to_a_different_row(
        self, destroy_view, monkeypatch
    ):
        # WidgetTag has two concrete relation fields: "widget" (the right related_model, but
        # pointing at a different Widget row than the one being deleted) and "tag" (the wrong
        # related_model entirely). Neither matches, so _protecting_field_name must walk past both
        # - "widget"'s pk mismatch (continuing the loop) and "tag"'s related_model mismatch - and
        # still land on field: None instead of stopping early.
        widget = Widget.objects.create(name="bolt", count=1)
        other_widget = Widget.objects.create(name="nut", count=2)
        tag = Tag.objects.create(label="unrelated")
        widget_tag = WidgetTag.objects.create(widget=other_widget, tag=tag)

        def fake_delete(*args, **kwargs):
            raise ProtectedError("protected", {widget_tag})

        monkeypatch.setattr(Widget, "delete", fake_delete)
        request = APIRequestFactory().delete(f"/widgets/{widget.pk}/")
        response = destroy_view(request, pk=widget.pk)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"protected_by": [{"model": "WidgetTag", "field": None}]}

    def test_a_reverse_relation_is_skipped_not_mistaken_for_a_concrete_match(self):
        # Widget has a reverse (non-concrete) relation to Comment (the other direction of
        # Comment.widget) - it must be skipped via is_relation-and-concrete, not treated as a
        # match: a reverse relation descriptor has no .attname, so treating it as concrete would
        # crash instead of cleanly falling through to field: None.
        widget = Widget(name="bolt", count=1)
        comment = Comment(body="hi", widget=widget)
        assert ProtectedDestroyMixin._protecting_field_name(widget, comment) is None

    @isolate_apps("tests.testapp")
    def test_continues_past_a_wrong_related_model_field_to_find_a_later_matching_one(self):
        class Blocker(models.Model):
            unrelated = models.ForeignKey(Tag, on_delete=models.CASCADE)  # wrong related_model, checked first
            widget = models.ForeignKey(Widget, on_delete=models.CASCADE)  # right related_model, checked second

            class Meta:
                app_label = "testapp"

        widget = Widget.objects.create(name="bolt", count=1)
        tag = Tag.objects.create(label="x")
        blocker = Blocker(unrelated=tag, widget=widget)

        assert ProtectedDestroyMixin._protecting_field_name(blocker, widget) == "widget"

    def test_groups_by_model_across_multiple_different_protecting_models(self, destroy_view, monkeypatch):
        # Comment and WidgetTag both have a real concrete "widget" FK, but they're different model
        # classes - this exercises sorted(blockers) grouping/ordering across more than one model,
        # not just multiple rows of the same one. Faked via monkeypatching for the same reason as
        # above: nothing in this schema naturally raises a single ProtectedError spanning two
        # distinct protecting models at once.
        widget = Widget.objects.create(name="bolt", count=1)
        comment = Comment.objects.create(body="hi", widget=widget)
        tag = Tag.objects.create(label="gizmo")
        widget_tag = WidgetTag.objects.create(widget=widget, tag=tag)

        def fake_delete(*args, **kwargs):
            raise ProtectedError("protected", {comment, widget_tag})

        monkeypatch.setattr(Widget, "delete", fake_delete)
        request = APIRequestFactory().delete(f"/widgets/{widget.pk}/")
        response = destroy_view(request, pk=widget.pk)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {
            "protected_by": [
                {"model": "Comment", "field": "widget"},
                {"model": "WidgetTag", "field": "widget"},
            ]
        }
