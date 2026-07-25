# request_context

`RequestContextMixin` - small helpers for reaching the current request/user from inside serializer logic (validators, `SerializerMethodField` getters, `get_extra_kwargs`, etc.) where `self.context` isn't always convenient to reach into directly.

```python
class WidgetSerializer(RequestContextMixin, ModelSerializer):
    def validate_name(self, value):
        if self.current_user() is None:
            raise serializers.ValidationError("Must be authenticated.")
        return value
```

- `current_request()` returns `None` with no request in context; `current_user()` returns `None` for both a missing request and an unauthenticated one.
