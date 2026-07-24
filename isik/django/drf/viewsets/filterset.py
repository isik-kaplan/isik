from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import classproperty
from django_filters.rest_framework import DjangoFilterBackend, FilterSet


class FilterSetMixin:
    """
    Builds `filterset_class` on the fly from `filterset_fields` + `declared_filters`, instead of
    hand-writing a FilterSet subclass for every viewset.

        class WidgetViewSet(FilterSetMixin, ModelViewSet):
            model = Widget
            filterset_fields = ["name", "owner"]
            declared_filters = {"name": CharFilter(lookup_expr="icontains")}

    If either is set, `DjangoFilterBackend` (or a subclass) must be present in `filter_backends` -
    otherwise DRF silently ignores `filterset_class` and the fields have no effect - so this fails
    loudly at class-definition time instead.
    """

    filterset_base = FilterSet
    filterset_fields = {}
    declared_filters = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not (cls.filterset_fields or cls.declared_filters):
            return
        filter_backends = getattr(cls, "filter_backends", [])
        if not any(issubclass(backend, DjangoFilterBackend) for backend in filter_backends):
            raise ImproperlyConfigured(
                f"{cls.__name__} sets filterset_fields/declared_filters but DjangoFilterBackend "
                "(or a subclass) is not in filter_backends - add it to "
                "REST_FRAMEWORK['DEFAULT_FILTER_BACKENDS'] or set filter_backends directly."
            )

    @classproperty
    def filterset_class(cls):
        meta_base = getattr(cls.filterset_base, "Meta", object)
        meta = type("Meta", (meta_base,), {"model": cls.model, "fields": cls.filterset_fields})
        return type("AutoFilterSet", (cls.filterset_base,), {**cls.declared_filters, "Meta": meta})
