# base

Two bases: one for any viewset, one for a viewset over one of your models.

## BaseViewSet

Everything isik adds to a viewset that isn't about a model, on `GenericViewSet`:
`RequestPoliciesMixin` (`request_policies`, see [request_policies.md](request_policies.md)),
`ActionSerializerClassMixin`, `GuardedFieldsMixin`. For a viewset over something that isn't one of
your models - a third-party library's tokens, a computed resource - which should still be held to the
same request-level rules.

```python
class AccessTokenViewSet(BaseViewSet):
    request_policies = [OrganizationIsSetUp]
    serializer_class = AccessTokenSerializer

    def list(self, request): ...
```

## BaseModelViewSet

`BaseViewSet` plus everything that is about a model, on `ModelViewSet`: `RequiredAttributesMixin`
(`model`/`endpoint`/`serializer_class` required), `ViewSetRegistryMixin`, `ProtectedDestroyMixin`,
`ReverseOrderingMixin`, `DeclaredOrderingMixin`, `FilterSetMixin`.

```python
class WidgetViewSet(BaseModelViewSet):
    model = Widget
    endpoint = "widgets"
    serializer_class = WidgetSerializer
    filterset_fields = ["name"]
    ordering_fields = ["created_at"]

# missing `endpoint` raises TypeError at class-definition time, before ViewSetRegistryMixin
# would otherwise register the (broken) class
```

- `model` is required explicitly and is the source of truth for `get_queryset()` - not `serializer_class.Meta.model` - matching the "required explicit" choice made for `endpoint` too.
- `model`/`endpoint`/`serializer_class` buy the model surface and nothing else. A request-level rule goes in `request_policies`, which `BaseViewSet` carries too - never give a viewset a `model` to get one.
- Pick and compose the individual mixins directly instead (see their own docs) if a project doesn't want the whole stack.
