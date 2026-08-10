import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory

from isik.django.drf.permissions import (
    IsAnonymous,
    IsAuthenticatedANDSignupCompleted,
    IsSuperUser,
    ReadOnly,
    is_owner,
    prevent_actions,
    user_property,
)
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


@pytest.fixture
def rf():
    return RequestFactory()


class FakeView:
    def __init__(self, action):
        self.action = action


class TestReadOnly:
    def test_allows_safe_methods(self, rf):
        assert ReadOnly().has_permission(rf.get("/"), view=None) is True

    def test_denies_unsafe_methods(self, rf):
        assert ReadOnly().has_permission(rf.post("/"), view=None) is False


class TestIsAnonymous:
    def test_allows_anonymous_users(self, rf):
        request = rf.get("/")
        request.user = AnonymousUser()
        assert IsAnonymous().has_permission(request, view=None) is True

    def test_denies_authenticated_users(self, rf, django_user_model):
        request = rf.get("/")
        request.user = django_user_model.objects.create_user(username="alice", password="password")
        assert IsAnonymous().has_permission(request, view=None) is False


class TestIsSuperUser:
    def test_allows_superusers(self, rf, django_user_model):
        request = rf.get("/")
        request.user = django_user_model.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )
        assert IsSuperUser().has_permission(request, view=None) is True

    def test_denies_regular_users(self, rf, django_user_model):
        request = rf.get("/")
        request.user = django_user_model.objects.create_user(username="alice", password="password")
        assert IsSuperUser().has_permission(request, view=None) is False

    def test_denies_when_there_is_no_user(self, rf):
        request = rf.get("/")
        request.user = None
        assert IsSuperUser().has_permission(request, view=None) is False


class TestIsAuthenticatedANDSignupCompleted:
    def test_allows_when_the_signup_completed_field_is_true(self, rf, django_user_model):
        user = django_user_model.objects.create_user(username="alice", password="password", is_active=True)
        user.SIGNUP_COMPLETED_FIELD = "is_active"
        request = rf.get("/")
        request.user = user
        assert IsAuthenticatedANDSignupCompleted().has_permission(request, view=None) is True

    def test_denies_when_the_signup_completed_field_is_false(self, rf, django_user_model):
        user = django_user_model.objects.create_user(username="alice", password="password", is_active=False)
        user.SIGNUP_COMPLETED_FIELD = "is_active"
        request = rf.get("/")
        request.user = user
        assert IsAuthenticatedANDSignupCompleted().has_permission(request, view=None) is False

    def test_denies_anonymous_users(self, rf):
        request = rf.get("/")
        request.user = AnonymousUser()
        assert IsAuthenticatedANDSignupCompleted().has_permission(request, view=None) is False

    def test_raises_improperly_configured_when_signup_completed_field_is_not_configured(self, rf, django_user_model):
        # SIGNUP_COMPLETED_FIELD is read directly off the user (not via getattr), so a user model
        # that never defines it is a misconfiguration that fails loudly rather than silently
        # denying access - with a clear, intentional exception instead of a bare AttributeError.
        user = django_user_model.objects.create_user(username="alice", password="password")
        request = rf.get("/")
        request.user = user
        with pytest.raises(ImproperlyConfigured, match="SIGNUP_COMPLETED_FIELD"):
            IsAuthenticatedANDSignupCompleted().has_permission(request, view=None)

    def test_denies_when_the_named_field_itself_is_missing_from_the_user(self, rf, django_user_model):
        # SIGNUP_COMPLETED_FIELD names a real field ("is_active"), but the field it points at
        # doesn't have to exist - getattr()'s own fallback has to default to denying, not granting.
        user = django_user_model.objects.create_user(username="alice", password="password")
        user.SIGNUP_COMPLETED_FIELD = "no_such_attribute"
        request = rf.get("/")
        request.user = user
        assert IsAuthenticatedANDSignupCompleted().has_permission(request, view=None) is False


class TestIsOwner:
    def test_allows_when_the_owner_field_matches_the_requesting_user(self, rf, django_user_model):
        user = django_user_model.objects.create_user(username="alice", password="password")
        widget = Widget.objects.create(name="bolt", count=1, owner=user)
        request = rf.get("/")
        request.user = user
        permission_cls = is_owner("owner")
        assert permission_cls().has_object_permission(request, view=None, obj=widget) is True

    def test_denies_when_the_owner_field_does_not_match(self, rf, django_user_model):
        owner = django_user_model.objects.create_user(username="alice", email="alice@example.com", password="password")
        other = django_user_model.objects.create_user(username="bob", email="bob@example.com", password="password")
        widget = Widget.objects.create(name="bolt", count=1, owner=owner)
        request = rf.get("/")
        request.user = other
        permission_cls = is_owner("owner")
        assert permission_cls().has_object_permission(request, view=None, obj=widget) is False

    def test_denies_when_the_owner_field_is_none(self, rf, django_user_model):
        user = django_user_model.objects.create_user(username="alice", password="password")
        widget = Widget.objects.create(name="bolt", count=1, owner=None)
        request = rf.get("/")
        request.user = user
        permission_cls = is_owner("owner")
        assert permission_cls().has_object_permission(request, view=None, obj=widget) is False

    def test_denies_instead_of_raising_when_the_object_has_no_such_attribute_at_all(self, rf, django_user_model):
        # Not the same as owner_field being None (see above) - this object doesn't have the
        # attribute at all, which getattr()'s default is the only thing standing between this and
        # an uncaught AttributeError.
        user = django_user_model.objects.create_user(username="alice", password="password")
        request = rf.get("/")
        request.user = user
        permission_cls = is_owner("nonexistent_field")
        assert permission_cls().has_object_permission(request, view=None, obj=object()) is False

    def test_generated_class_name_includes_the_owner_field(self):
        assert is_owner("owner").__name__ == "IsOwnerPermission(owner_field=owner)"

    def test_denial_message(self):
        assert is_owner("owner").message == "User is not the owner of the object"


class TestPreventActions:
    def test_denies_the_listed_actions(self, rf):
        permission_cls = prevent_actions("create", "destroy")
        assert permission_cls().has_permission(rf.get("/"), FakeView(action="destroy")) is False

    def test_allows_actions_not_in_the_list(self, rf):
        permission_cls = prevent_actions("create", "destroy")
        assert permission_cls().has_permission(rf.get("/"), FakeView(action="list")) is True

    def test_generated_class_name_includes_the_actions(self):
        permission_cls = prevent_actions("create", "destroy")
        assert "create" in permission_cls.__name__
        assert "destroy" in permission_cls.__name__

    def test_denial_message(self):
        assert prevent_actions("create", "destroy").message == "Actions should not be: ('create', 'destroy')"


class TestUserProperty:
    def test_requires_exactly_one_of_property_or_attribute(self):
        with pytest.raises(ValueError, match="^user_property requires exactly one of property_ or attribute$"):
            user_property()

    def test_generated_class_name_and_default_message_use_the_attribute_name(self):
        permission_cls = user_property(attribute="is_verified")
        assert permission_cls.__name__ == "UserAttributePermission(property=is_verified)"
        assert permission_cls.message == "User property is_verified is False"

    def test_generated_class_name_and_default_message_use_the_property_name(self):
        class User:
            @property
            def is_verified(self):
                return True

        permission_cls = user_property(property_=User.is_verified)
        assert permission_cls.__name__ == "UserAttributePermission(property=is_verified)"
        assert permission_cls.message == "User property is_verified is False"

    def test_rejects_both_property_and_attribute_together(self):
        class User:
            @property
            def is_verified(self):
                return True

        with pytest.raises(ValueError):
            user_property(property_=User.is_verified, attribute="is_verified")

    def test_works_with_a_property(self, rf):
        class User:
            @property
            def is_verified(self):
                return True

        permission_cls = user_property(property_=User.is_verified)
        request = rf.get("/")
        request.user = User()
        assert permission_cls().has_permission(request, view=None) is True

    def test_works_with_an_attribute_name(self, rf):
        class User:
            is_verified = False

        permission_cls = user_property(attribute="is_verified")
        request = rf.get("/")
        request.user = User()
        assert bool(permission_cls().has_permission(request, view=None)) is False

    def test_object_permission_uses_the_same_check(self, rf):
        class User:
            is_verified = True

        permission_cls = user_property(attribute="is_verified")
        request = rf.get("/")
        request.user = User()
        assert permission_cls().has_object_permission(request, view=None, obj=object()) is True

    def test_a_reason_attribute_on_the_value_becomes_the_denial_message(self, rf):
        class Unverified:
            reason = "email not confirmed"

            def __bool__(self):
                return False

        class User:
            is_verified = Unverified()

        permission_cls = user_property(attribute="is_verified")
        permission = permission_cls()
        request = rf.get("/")
        request.user = User()

        assert not permission.has_permission(request, view=None)
        assert permission.message == "email not confirmed"

    def test_a_reason_attribute_is_also_used_for_object_permission_denials(self, rf):
        class Unverified:
            reason = "email not confirmed"

            def __bool__(self):
                return False

        class User:
            is_verified = Unverified()

        permission_cls = user_property(attribute="is_verified")
        permission = permission_cls()
        request = rf.get("/")
        request.user = User()

        assert not permission.has_object_permission(request, view=None, obj=object())
        assert permission.message == "email not confirmed"

    def test_denies_permission_instead_of_crashing_when_user_lacks_the_attribute(self, rf):
        class AnonymousUser:
            pass

        permission_cls = user_property(attribute="is_verified")
        request = rf.get("/")
        request.user = AnonymousUser()
        assert permission_cls().has_permission(request, view=None) is False

    def test_denies_object_permission_instead_of_crashing_when_user_lacks_the_attribute(self, rf):
        class AnonymousUser:
            pass

        permission_cls = user_property(attribute="is_verified")
        request = rf.get("/")
        request.user = AnonymousUser()
        assert permission_cls().has_object_permission(request, view=None, obj=object()) is False
