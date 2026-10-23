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

## MatchModeFilter

A comma-separated list of values over a to-many relation, matched by any or all of them - a second
query parameter, `mode_param`, says which:

```python
from django_filters import FilterSet
from isik.django.drf.filters import MatchModeFilter


class RosterFilterSet(FilterSet):
    tags = MatchModeFilter(field_name="tags__label", mode_param="tags_match")
```

```
?tags=red,blue                  -> rosters with a red or a blue tag
?tags=red,blue&tags_match=all   -> rosters with both
```

- `any` (the default) and `all` each narrow with one join: `all` counts the distinct values a row
  matches and keeps those matching every one, rather than joining the relation once per value.
- A value given twice counts once.
- Another mode is a 400 naming the parameter: `{"tags_match": ["Must be one of: any, all."]}`.
- `mode_param` is required, and keyword-only. `MATCH_MODES` holds the modes.
