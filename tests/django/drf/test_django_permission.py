import enum
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser, Permission
from django.test import RequestFactory
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from isik._internal.translation import gettext_lazy
from isik.django.drf.permissions import IsSuperUser, django_permission, guarding
from isik.django.drf.viewsets import GuardedFieldsMixin
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db

VIEW_WIDGET = "testapp.view_widget"


@pytest.fixture
def holder(django_user_model):
    user = django_user_model.objects.create_user(username="holder", email="holder@example.com", password="password")
    user.user_permissions.add(Permission.objects.get(codename="view_widget", content_type__app_label="testapp"))
    return django_user_model.objects.get(pk=user.pk)  # a fresh instance - has_perm caches per object


@pytest.fixture
def stranger(django_user_model):
    return django_user_model.objects.create_user(username="stranger", email="stranger@example.com", password="password")


def request_by(user):
    request = RequestFactory().get("/")
    request.user = user
    return request


def asks(permission_cls, user):
    return permission_cls().has_permission(request_by(user), view=None)


class TestWhoPasses:
    def test_a_holder_passes(self, holder):
        assert asks(django_permission(VIEW_WIDGET), holder) is True

    def test_anyone_else_is_refused(self, stranger):
        assert asks(django_permission(VIEW_WIDGET), stranger) is False

    def test_an_active_superuser_passes_under_model_backend(self, django_user_model):
        root = django_user_model.objects.create_superuser(username="root", email="root@example.com", password="x")
        assert asks(django_permission(VIEW_WIDGET), root) is True

    def test_an_inactive_holder_is_refused_under_model_backend(self, holder):
        holder.is_active = False
        assert asks(django_permission(VIEW_WIDGET), holder) is False

    def test_an_anonymous_caller_is_refused_without_asking_the_backend(self):
        class Anonymous:
            is_authenticated = False

            def has_perm(self, permission):
                raise AssertionError("the backend must not be asked")

        assert asks(django_permission(VIEW_WIDGET), Anonymous()) is False
        assert asks(django_permission(VIEW_WIDGET), AnonymousUser()) is False

    def test_a_request_with_no_user_at_all_is_refused(self):
        assert django_permission(VIEW_WIDGET)().has_permission(SimpleNamespace(), view=None) is False
        assert asks(django_permission(VIEW_WIDGET), None) is False

    def test_the_answer_is_a_bool_whatever_the_backend_returns(self):
        class Generous:
            is_authenticated = True

            def has_perm(self, permission):
                return "yes"

        assert asks(django_permission(VIEW_WIDGET), Generous()) is True

    def test_the_backend_gets_the_permission_object_itself(self):
        class Declaration(str):
            pass

        permission = Declaration("invitations:issue:invitation")
        seen = []

        class Recording:
            is_authenticated = True

            def has_perm(self, value):
                seen.append(value)
                return True

        permission_cls = django_permission(permission)
        asks(permission_cls, Recording())
        assert seen[0] is permission
        assert permission_cls.permission is permission

    def test_it_is_request_level_only(self, stranger):
        assert django_permission(VIEW_WIDGET)().has_object_permission(request_by(stranger), None, object()) is True


class TestWhatItTakes:
    def test_a_str_enum_member_is_used_as_the_string_it_is(self):
        class Perm(enum.StrEnum):
            VIEW = VIEW_WIDGET

        assert django_permission(Perm.VIEW).permission is Perm.VIEW
        assert django_permission(Perm.VIEW).__name__ == "HasTestappViewWidget"

    def test_a_plain_enum_member_contributes_its_value(self, holder):
        class Perm(enum.Enum):
            VIEW = VIEW_WIDGET

        permission_cls = django_permission(Perm.VIEW)
        assert permission_cls.permission == VIEW_WIDGET
        assert asks(permission_cls, holder) is True

    def test_an_enum_whose_value_is_not_a_string_is_refused(self):
        class Numbered(enum.Enum):
            ONE = 1

        with pytest.raises(TypeError, match=r"^django_permission takes a permission name, or an Enum member whose"):
            django_permission(Numbered.ONE)

    def test_anything_else_is_refused(self):
        with pytest.raises(TypeError, match=r"- not 5$"):
            django_permission(5)

    def test_an_empty_name_is_refused(self):
        with pytest.raises(ValueError, match=r"^django_permission needs a permission name, not an empty string$"):
            django_permission("")

    def test_a_message_of_the_wrong_kind_is_refused(self):
        with pytest.raises(
            TypeError, match=r"^django_permission's message= takes a template, a message or a callable - not 5$"
        ):
            django_permission(VIEW_WIDGET, message=5)


class TestNames:
    @pytest.mark.parametrize(
        ("permission", "name"),
        [
            (VIEW_WIDGET, "HasTestappViewWidget"),
            ("invitations:issue:public_invitation", "HasInvitationsIssuePublicInvitation"),
        ],
    )
    def test_named_from_the_permission(self, permission, name):
        assert django_permission(permission).__name__ == name

    def test_an_explicit_name(self):
        assert django_permission(VIEW_WIDGET, name="MaySeeWidgets").__name__ == "MaySeeWidgets"


class TestMessages:
    def refused(self, permission_cls, user):
        permission = permission_cls()
        assert permission.has_permission(request_by(user), view="the view") is False
        return str(permission.message)

    def test_the_default_names_the_permission(self, stranger):
        assert self.refused(django_permission(VIEW_WIDGET), stranger) == (
            "You need the testapp.view_widget permission to do this."
        )

    def test_a_template_gets_the_permission_filled_in(self, stranger):
        permission_cls = django_permission(VIEW_WIDGET, message="Only %(permission)s holders, 100%% sure.")
        assert self.refused(permission_cls, stranger) == "Only testapp.view_widget holders, 100% sure."

    def test_a_fixed_message_is_used_as_it_is(self, stranger):
        assert self.refused(django_permission(VIEW_WIDGET, message="Ask an admin - 100% of the time."), stranger) == (
            "Ask an admin - 100% of the time."
        )

    def test_a_lazy_template_is_translated_in_the_language_of_each_refusal(self, stranger, monkeypatch):
        language = {"current": "en"}
        monkeypatch.setattr(
            "django.utils.translation.gettext",
            lambda text: text.replace("Only", "Sadece") if language["current"] == "tr" else text,
        )
        permission_cls = django_permission(VIEW_WIDGET, message=gettext_lazy("Only %(permission)s holders."))
        assert self.refused(permission_cls, stranger) == "Only testapp.view_widget holders."
        language["current"] = "tr"
        assert self.refused(permission_cls, stranger) == "Sadece testapp.view_widget holders."

    def test_the_default_is_translated_too(self, stranger, monkeypatch):
        monkeypatch.setattr("django.utils.translation.gettext", lambda text: text.replace("You need", "Gerekli:"))
        assert self.refused(django_permission(VIEW_WIDGET), stranger).startswith("Gerekli: the testapp.view_widget")

    def test_a_callable_is_asked_on_refusal_with_the_permission_request_and_view(self, stranger, holder):
        calls = []

        def refusal(permission, request, view):
            calls.append((permission, request.user, view))
            return f"no {permission} for {request.user.username}"

        permission_cls = django_permission(VIEW_WIDGET, message=refusal)
        assert self.refused(permission_cls, stranger) == "no testapp.view_widget for stranger"
        assert calls == [(VIEW_WIDGET, stranger, "the view")]
        assert asks(permission_cls, holder) is True
        assert len(calls) == 1  # not asked when the request passes

    def test_the_class_message_behaves_as_a_string(self):
        assert django_permission(VIEW_WIDGET).message.upper() == (
            "YOU NEED THE TESTAPP.VIEW_WIDGET PERMISSION TO DO THIS."
        )

    def test_with_a_callable_the_class_message_is_the_default(self):
        permission_cls = django_permission(VIEW_WIDGET, message=lambda **kwargs: "custom")
        assert str(permission_cls.message) == "You need the testapp.view_widget permission to do this."


class TestInUse:
    def view(self, *permission_classes):
        return type(
            "ProbeView",
            (APIView,),
            {"permission_classes": permission_classes, "get": lambda self, request: Response({"ok": True})},
        ).as_view()

    def get(self, view, user):
        request = APIRequestFactory().get("/")
        force_authenticate(request, user=user)
        return view(request)

    def test_a_refusal_is_a_403_with_the_message(self, stranger):
        response = self.get(self.view(django_permission(VIEW_WIDGET, message="Widgets are private.")), stranger)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data == {"detail": "Widgets are private."}

    def test_a_callable_message_reaches_the_response(self, stranger):
        view = self.view(django_permission(VIEW_WIDGET, message=lambda permission, request, view: "computed"))
        assert self.get(view, stranger).data == {"detail": "computed"}

    def test_it_composes(self, stranger, django_user_model):
        root = django_user_model.objects.create_superuser(username="boss", email="boss@example.com", password="x")
        view = self.view(django_permission("testapp.delete_widget") | IsSuperUser)
        assert self.get(view, root).status_code == 200
        assert self.get(view, stranger).status_code == 403

    def test_inside_a_field_guard_it_is_asked_on_create_too(self, holder, stranger):
        class WidgetSerializer(serializers.ModelSerializer):
            class Meta:
                model = Widget
                fields = ["id", "name", "count"]

        viewset = type(
            "PermissionGuardedViewSet",
            (GuardedFieldsMixin, ModelViewSet),
            {
                "queryset": Widget.objects.all(),
                "serializer_class": WidgetSerializer,
                "permission_classes": [guarding(django_permission(VIEW_WIDGET), fields=["count"])],
            },
        )

        def create(user, data):
            request = APIRequestFactory().post("/widgets/", data, format="json")
            force_authenticate(request, user=user)
            return viewset.as_view({"post": "create"})(request)

        assert create(stranger, {"name": "a", "count": 2}).status_code == 400
        assert create(stranger, {"name": "b"}).status_code == 201
        assert create(holder, {"name": "c", "count": 2}).status_code == 201
