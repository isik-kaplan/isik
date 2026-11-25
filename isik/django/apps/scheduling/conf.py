"""
`ISIK['DJANGO']['SCHEDULING']`, the setting the scheduling app reads:

    ISIK['DJANGO']['SCHEDULING'] = {"TASKS": "billing.scheduled_tasks", "TASK_MODEL": "ops.ScheduledTaskState"}

Keys this module doesn't know are refused by a system check, `isik.E031`.
"""

from isik.django.conf import django_area


# TASK_MODEL - the task state model, "app_label.Model" - is read by swapping.py: isik's, unless the project owns it.
KEYS = (
    # The module declaring the project's scheduled tasks - imported wherever the declarations are read.
    "TASKS",
    "TASK_MODEL",
)


def configured():
    """`ISIK['DJANGO']['SCHEDULING']` as the project set it."""
    area = django_area("SCHEDULING")
    return area if isinstance(area, dict) else {}
