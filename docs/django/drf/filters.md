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
- Alone, `mode_param` is read but not declared: a schema doesn't show it, and nor does a client generated
  from one. `match_mode_filters()` declares both - the filter, and a `MatchModeChoiceFilter` for its mode,
  which narrows nothing but is checked against `MATCH_MODES` and published with them:

```python
from isik.django.drf.filters import match_mode_filters


class RosterFilterSet(FilterSet):
    locals().update(match_mode_filters("tags", field_name="tags__label"))
    # produces: tags, tags_match - mode_param= names another
```

### Over rows - `ModelMatchModeFilter`

Where the values name rows, `queryset=` makes the filter a `ModelMatchModeFilter` - a
`ModelMultipleChoiceFilter` matched by any or all in the same one join. Each value repeats the parameter,
and one naming no row of `queryset` is a 400 naming the filter, not a page of nothing:

```python
class TokenFilterSet(FilterSet):
    locals().update(
        match_mode_filters("scopes", field_name="scopes", queryset=Permission.objects.all(), to_field_name="codename")
    )
```

```
?scopes=read&scopes=write                    -> tokens with either
?scopes=read&scopes=write&scopes_match=all   -> tokens with both
?scopes=nonsense                             -> 400
```
