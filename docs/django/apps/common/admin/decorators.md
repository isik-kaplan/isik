# decorators

`action(description)` decorates a `ModelAdmin` method as a Django admin action: sets its
`short_description`, normalizes a single instance into a one-item queryset (via
`django-object-actions`' `takes_instance_or_queryset`, so the same method works as both a list
action and a detail/object action), and wraps the whole call in `transaction.atomic`.

```python
from isik.django.apps.common.admin import action

class WidgetAdmin(BaseAdmin):
    @action("Approve")
    def approve(self, request, queryset):
        queryset.update(approved=True)
```

- Wrapped in `atomic` — an exception raised anywhere inside rolls back everything the action did,
  including writes made before the exception.
