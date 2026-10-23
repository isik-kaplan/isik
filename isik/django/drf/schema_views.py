"""SpectacularAPIView - see its own docstring.

Apart from `isik.django.drf.spectacular`, which `DEFAULT_SCHEMA_CLASS` names: drf-spectacular's views
read that setting as they load, so a module holding both would import itself half-made.
"""

from django.conf import settings
from django.utils import translation
from drf_spectacular.views import SpectacularAPIView as _BaseSpectacularAPIView


class SpectacularAPIView(_BaseSpectacularAPIView):
    """
    drf-spectacular's schema view, generating the document in `ISIK_SCHEMA_LANGUAGE` whatever the
    request's own language - so every client gets, and every cache keeps, one document. A `?lang=`
    still picks another. Without the setting it's drf-spectacular's own.

        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    """

    def _get_schema_response(self, request):
        language = getattr(settings, "ISIK_SCHEMA_LANGUAGE", None)
        if language is None or request.GET.get("lang"):
            return super()._get_schema_response(request)
        with translation.override(language):
            return super()._get_schema_response(request)
