"""Middleware - see its own docstring."""


class Middleware:
    """
    The constructor every hand-written Django middleware opens with, and a `__call__` around two
    hooks - so a middleware is only what it does:

        class ServedBy(Middleware):
            def after(self, request, response):
                response["X-Served-By"] = settings.HOSTNAME
                return response

        class Maintenance(Middleware):
            def before(self, request):
                if settings.MAINTENANCE:
                    return HttpResponse(status=503)  # answers here; the view never runs

    - `before(request)` runs first. Returning a response answers the request with it - the rest of
      the chain and the view never run, and `after()` still sees it. `None` carries on.
    - `after(request, response)` runs on the way out, and returns the response to send.

    Django's other hooks - `process_view`, `process_exception`, `process_template_response` - are
    Django's own and stay so: define them on a subclass as on any middleware. Override `__call__`
    when the work has to wrap the view, as a context manager or a `try`/`finally` does.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.before(request)
        if response is None:
            response = self.get_response(request)
        return self.after(request, response)

    def before(self, request):
        """Runs before the view - return a response to answer the request with it instead."""
        return None

    def after(self, request, response):
        """Runs after the view - return the response to send."""
        return response
