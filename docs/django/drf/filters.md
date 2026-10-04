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

## NarrowingFilterMixin

A django-filter filter that leaves the queryset alone when its value wasn't given, and narrows it
with `narrow(qs, value)` when it was. django-filter's own `Filter.filter()` skips an empty value, but
overriding `filter()` loses that - so every custom filter writes `if value in EMPTY_VALUES: return qs`
again. Mix this in before the filter class instead:

```python
from django_filters import DateFilter
from isik.django.drf.filters import NarrowingFilterMixin


class PublishedAfterFilter(NarrowingFilterMixin, DateFilter):
    def narrow(self, qs, value):
        return qs.filter(published_at__date__gt=value)
```
