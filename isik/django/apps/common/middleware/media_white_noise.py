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
        script_prefix = get_script_prefix().rstrip("/")  # pragma: no mutate
        # rstrip("/") vs rstrip() (Django's script prefix never has trailing whitespace to strip
        # differently) only changes whether the leading "/" of what's left is stripped here or by
        # ensure_leading_trailing_slash() below - either way the final static_prefix is identical.
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
        del http_response["content-type"]  # pragma: no mutate
        # Django's response headers are a case-insensitive mapping, so a case-variant of this
        # literal (e.g. "Content-Type") deletes the exact same header - only a wrong key entirely
        # would matter, and that's what test_a_304_response_has_no_content_type_header covers.
        for key, value in response.headers:
            http_response[key] = value
        return http_response
