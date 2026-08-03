# registry

`ViewSetRegistryMixin` maintains a model -> viewset-class registry, populated automatically as subclasses are defined - so you can look up "what's the API endpoint for this model" later (e.g. to build a cross-link, or a notification pointing at some other resource).

```python
class WidgetViewSet(ViewSetRegistryMixin, ModelViewSet):
    model = Widget

ViewSetRegistryMixin.get_for_model(Widget) is WidgetViewSet
ViewSetRegistryMixin.get_for_model(Unregistered)  # None, not KeyError
```

- Registering two viewsets for the same model raises `ImproperlyConfigured` immediately. A subclass with no `model` set yet (e.g. an abstract intermediate relying on `RequiredAttributesMixin` to enforce it later) is skipped rather than registered under a placeholder - as is one with `exempt_from_registry = True`.
- Set `is_base_class = True` on a project-level intermediate to give that branch its own private registry instead of sharing this one - several independent hierarchies (e.g. one `BaseModelViewSet` per API) can then each register the same model without colliding, and without needing `exempt_from_registry = True` sprinkled across every one of them.

    ```python
    class AuthBaseModelViewSet(BaseModelViewSet):
        is_base_class = True

    class AuthWidgetViewSet(AuthBaseModelViewSet):
        model = Widget

    AuthBaseModelViewSet.get_for_model(Widget) is AuthWidgetViewSet
    ViewSetRegistryMixin.get_for_model(Widget)  # None - a different registry entirely
    ```
