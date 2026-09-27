from isik._internal import check_extra


check_extra("celery", "celery")

from isik.django.celery.tasks import HistoryContextTask  # noqa: E402


__all__ = [
    "HistoryContextTask",
]
