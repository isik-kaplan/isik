from urllib.parse import urlparse

from django.conf import settings
from django.urls import get_script_prefix
from whitenoise.base import WhiteNoise
from whitenoise.middleware import WhiteNoiseFileResponse
from whitenoise.string_utils import ensure_leading_trailing_slash


class MediaWhiteNoiseMiddleware(WhiteNoise):
    def __init__(self, get_response=None):
        self.get_response = get_response

        static_prefix = urlparse(settings.MEDIA_URL or "").path
        script_prefix = get_script_prefix().rstrip("/")
        if script_prefix and static_prefix.startswith(script_prefix):
            static_prefix = static_prefix[len(script_prefix) :]
        static_prefix = ensure_leading_trailing_slash(static_prefix)

        super().__init__(
            application=None,
            root=settings.MEDIA_ROOT,
            prefix=static_prefix,
            autorefresh=True,
            max_age=0,
        )

    def __call__(self, request):
        if settings.DEBUG:
            static_file = self.find_file(request.path_info)
            if static_file is not None:
                return self.serve(static_file, request)
        return self.get_response(request)

    @staticmethod
    def serve(static_file, request):
        response = static_file.get_response(request.method, request.META)
        http_response = WhiteNoiseFileResponse(response.file or (), status=int(response.status))
        del http_response["content-type"]
        for key, value in response.headers:
            http_response[key] = value
        return http_response
