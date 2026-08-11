from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class UsernameOREmailModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        if username is None:
            username = kwargs.get(user_model.USERNAME_FIELD)
        if not username:
            return None
        try:
            lookup = Q(**{user_model.EMAIL_FIELD: username}) | Q(**{user_model.USERNAME_FIELD: username})
            user = user_model._default_manager.get(lookup)
        except user_model.DoesNotExist:
            # Run the default password hasher once to reduce the timing
            # difference between an existing and a non-existing user (#20760).
            user_model().set_password(password)  # pragma: no mutate
            # This instance is local and never saved/returned - set_password()'s only real effect
            # is the CPU time it burns, not the resulting hash, so which value it hashes (the
            # caller's password vs. e.g. None) is unobservable through any assertion.
        else:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
