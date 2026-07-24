# drf.py stays explicit-import-only - it pulls in rest_framework, which this package doesn't
# otherwise require.
from isik.django.apps.feedback.bookmarks._bookmarks import UserBookmarkMixin, bookmarks


__all__ = [
    "UserBookmarkMixin",
    "bookmarks",
]
