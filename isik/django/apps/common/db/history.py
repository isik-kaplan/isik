import pghistory
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from pghistory.core import DeleteEvent, InsertEvent, UpdateEvent
from pghistory.middleware import HistoryMiddleware


def track_events(**kwargs):
    def track_model_history(cls):
        """
        Instead of using pghistory.track() directly, if we need base configuration we will do it here.
        """
        trackers = [InsertEvent(), UpdateEvent(), DeleteEvent()]
        return pghistory.track(*trackers, **kwargs)(cls)

    return track_model_history


def event_model_for(model):
    """
    The Event model django-pghistory generated for `model` via `@track_events()`.

        event_model_for(Widget).objects.filter(pgh_obj_id=widget.pk)

    Raises ImproperlyConfigured if `model` isn't tracked, or is tracked under more than one Event
    model (`pghistory.track()` applied more than once with different labels).
    """
    if "pgh_event_model" not in dir(model):
        raise ImproperlyConfigured(f"{model.__name__} has no @track_events() history to serve.")
    try:
        return model.pgh_event_model
    except ValueError as e:
        raise ImproperlyConfigured(str(e)) from e


def history_middleware_installed():
    """
    True if `pghistory.middleware.HistoryMiddleware` (or a subclass) is in `settings.MIDDLEWARE` -
    that's what stamps `user`/`url` into pghistory's context, so tracked events carry an actor.
    """
    for path in settings.MIDDLEWARE:
        try:
            middleware_cls = import_string(path)
        except ImportError:
            continue
        if isinstance(middleware_cls, type) and issubclass(middleware_cls, HistoryMiddleware):
            return True
    return False
