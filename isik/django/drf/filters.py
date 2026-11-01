from django.db.models import Count
from django_filters import BaseInFilter, CharFilter, ChoiceFilter, ModelMultipleChoiceFilter
from django_filters.constants import EMPTY_VALUES
from rest_framework.exceptions import ValidationError

from isik._internal.translation import gettext as _
from isik._internal.translation import lazy_format


def make_filters(field, filter_cls, lookup_expressions):
    """
    Builds a dict of django-filter filters for a single field across several lookup
    expressions, suitable for assigning to a FilterSet's declarative filters.

    Example:
        class MyFilterSet(FilterSet):
            locals().update(make_filters("created_at", DateTimeFilter, ["exact", "gte", "lte"]))
            # produces: created_at, created_at__gte, created_at__lte
    """
    get_key = lambda expr: f"{field}__{expr}" if expr != "exact" else field  # NOQA
    return {get_key(expr): filter_cls(field_name=field, lookup_expr=expr) for expr in lookup_expressions}


class NarrowingFilterMixin:
    """
    A django-filter filter that leaves the queryset alone when its value wasn't given, and narrows it
    with `narrow(qs, value)` when it was - mix in before the filter class:

        class PublishedAfterFilter(NarrowingFilterMixin, DateFilter):
            def narrow(self, qs, value):
                return qs.filter(published_at__date__gt=value)

    django-filter's own `Filter.filter()` skips an empty value, but overriding `filter()` loses that,
    so every custom filter writes `if value in EMPTY_VALUES: return qs` again. This writes it once.
    """

    def filter(self, qs, value):
        if value in EMPTY_VALUES:
            return qs
        return self.narrow(qs, value)

    def narrow(self, qs, value):
        """The queryset narrowed by `value` - called only with a value that was given."""
        raise NotImplementedError


MATCH_MODES = ("any", "all")


class _MatchesAnyOrAll:
    """Narrows by a to-many relation's values, any or all of them - the mode read from `mode_param`."""

    def __init__(self, *args, mode_param, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode_param = mode_param

    def filter(self, qs, value):
        # Nothing given: no values, or - for rows - the empty queryset a ModelMultipleChoiceField cleans it to.
        if not value:
            return qs
        values = list(dict.fromkeys(value))
        mode = self.parent.data.get(self.mode_param) or "any"
        if mode not in MATCH_MODES:
            raise ValidationError(
                {self.mode_param: [_("Must be one of: %(modes)s.") % {"modes": ", ".join(MATCH_MODES)}]}
            )
        matching = qs.filter(**{f"{self.field_name}__in": values}).values("pk")
        if mode == "all":
            matching = matching.annotate(matched=Count(self.field_name, distinct=True)).filter(matched=len(values))
        return qs.filter(pk__in=matching.values("pk"))


class MatchModeFilter(_MatchesAnyOrAll, BaseInFilter, CharFilter):
    """
    A comma-separated list of values over a to-many relation, matched by any or all of them - a second
    query parameter, `mode_param`, says which:

        class RosterFilterSet(FilterSet):
            tags = MatchModeFilter(field_name="tags__label", mode_param="tags_match")

        ?tags=red,blue                  -> rosters with a red or a blue tag
        ?tags=red,blue&tags_match=all   -> rosters with both

    `any` (the default) and `all` each narrow with one join: `all` counts the distinct values a row
    matches and keeps those matching every one, rather than joining the relation once per value.
    Another mode is a 400 naming the parameter.

    Alone, `mode_param` is read but not declared, so a schema never shows it - `match_mode_filters()`
    declares the pair.
    """


class ModelMatchModeFilter(_MatchesAnyOrAll, ModelMultipleChoiceFilter):
    """
    `MatchModeFilter` over rows rather than strings - a `ModelMultipleChoiceFilter`: each value repeats the
    parameter, and one naming no row of `queryset` is a 400 naming the filter, rather than a page of
    nothing.

        class TokenFilterSet(FilterSet):
            scopes = ModelMatchModeFilter(
                field_name="scopes", queryset=Permission.objects.all(), to_field_name="codename",
                mode_param="scopes_match",
            )

        ?scopes=read&scopes=write&scopes_match=all   -> tokens with both
    """


class MatchModeChoiceFilter(ChoiceFilter):
    """
    The mode a `MatchModeFilter` reads, declared as a parameter of its own - so the FilterSet checks it
    against `MATCH_MODES`, and a schema publishes it with them. It narrows nothing itself.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("choices", [(mode, mode) for mode in MATCH_MODES])
        super().__init__(*args, **kwargs)

    def filter(self, qs, value):
        return qs


def match_mode_filters(name, *, field_name, mode_param=None, queryset=None, **kwargs):
    """
    `{name: MatchModeFilter, mode_param: MatchModeChoiceFilter}` - the filter and its mode, both declared,
    for a FilterSet's declarative filters. `mode_param` is `<name>_match` unless given; `kwargs` are the
    filter's own options. With `queryset`, the filter is a `ModelMatchModeFilter`, taking rows of it.

        class RosterFilterSet(FilterSet):
            locals().update(match_mode_filters("tags", field_name="tags__label"))
            # produces: tags, tags_match
    """
    mode_param = mode_param or f"{name}_match"
    if queryset is None:
        values = MatchModeFilter(field_name=field_name, mode_param=mode_param, **kwargs)
    else:
        values = ModelMatchModeFilter(field_name=field_name, queryset=queryset, mode_param=mode_param, **kwargs)
    return {
        name: values,
        mode_param: MatchModeChoiceFilter(
            help_text=lazy_format("Whether a row matches any of %(name)s's values, or all of them.", name=name)
        ),
    }
