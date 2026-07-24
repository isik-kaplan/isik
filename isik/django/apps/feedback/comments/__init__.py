# drf.py and tiptap.py stay explicit-import-only - they pull in rest_framework/prosemirror,
# which this package doesn't otherwise require.
from isik.django.apps.feedback.comments._comments import UserCommentMixin, comments


__all__ = [
    "UserCommentMixin",
    "comments",
]
