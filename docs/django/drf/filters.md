# filters

`make_filters` builds a dict of django-filter filters for one field across several lookup expressions, so a `FilterSet` doesn't need `created_at`, `created_at__gte`, `created_at__lte` declared by hand one at a time.

```python
from django_filters import DateTimeFilter
from isik.django.drf.filters import make_filters

class MyFilterSet(FilterSet):
    locals().update(make_filters("created_at", DateTimeFilter, ["exact", "gte", "lte"]))
    # produces: created_at, created_at__gte, created_at__lte
```

- `"exact"` maps to the bare field name (no suffix); every other lookup gets a `field__lookup` key.
