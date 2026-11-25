"""
Staff controls over the scheduled tasks `isik.django.scheduling` declares: a task's state kept in a row of the
project's own model (`AbstractScheduledTaskState`), isik's beat scheduler running the tasks as declarations and rows
say, and a REST API letting staff do to each what its declaration allows. Add `"isik.django.apps.scheduling"` to
INSTALLED_APPS, set `CELERY_BEAT_SCHEDULER = "isik.django.apps.scheduling.scheduler:Scheduler"`, and route
`drf.ScheduledTaskViewSet`. Needs `pip install isik[scheduling]`.
"""
