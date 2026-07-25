# auth

`UsernameOREmailModelBackend` is a Django auth backend that authenticates against either
`USERNAME_FIELD` or `EMAIL_FIELD`, for when users should be able to log in with either their
username or their email regardless of which one is the actual `USERNAME_FIELD`.

```python
# settings.py
AUTHENTICATION_BACKENDS = ["isik.django.apps.common.backends.auth.UsernameOREmailModelBackend"]
```

- Always binds to the explicit `username` parameter, falling back to
  `kwargs.get(user_model.USERNAME_FIELD)` only if it's `None` — never reads from `**kwargs` when
  `username` was already given.
- Runs `user_model().set_password(password)` on a non-existent user before returning `None`, to
  keep response timing similar between "no such user" and "wrong password" (Django ticket #20760).
