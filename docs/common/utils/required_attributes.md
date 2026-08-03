# required_attributes

`RequiredAttributesMixin` fails fast at class-definition time if a subclass forgets to override one of `required_attributes`, instead of surfacing as an `AttributeError` (or silently wrong behavior) the first time it's actually used.

```python
from isik.common.utils.required_attributes import REQUIRED, RequiredAttributesMixin

class ViewSet(RequiredAttributesMixin):
    required_attributes = ["endpoint"]
    endpoint = REQUIRED

class WidgetViewSet(ViewSet):
    pass  # raises TypeError: WidgetViewSet must define an `endpoint` attribute

class GadgetViewSet(ViewSet):
    endpoint = "/gadgets"  # OK
```

- The class that declares `required_attributes` (`ViewSet` above) is itself exempt from the check - it's the one setting the `REQUIRED` sentinels, not the one meant to override them.
- The check runs before delegating to `super().__init_subclass__()`, so it fires ahead of any other mixin's `__init_subclass__` logic further down the MRO.
- Set `is_base_class = True` on a class that's meant to stay abstract a while longer (e.g. a project's own `class BaseModelViewSet(_BaseModelViewSet): is_base_class = True`) to exempt just that one class, without redeclaring `required_attributes` as a workaround. Both exemptions are read from the class's own `__dict__`, so they apply only to the class that sets them directly, not its descendants.
