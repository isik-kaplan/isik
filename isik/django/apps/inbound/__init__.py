"""
`isik.django.apps.inbound` - webhooks a project receives: each one verified, written down once - a sender's resend
of an event already received is acknowledged and dropped - answered at once, and handled in a Celery task, tried
again on a backoff until it's handled or given up on.

Declare each sender as a source (`sources.declare_source()`), its handlers with `sources.handles()`, route
`views.inbound_urls()`, configure with `ISIK['DJANGO']['INBOUND']` (see `conf`), and run `tasks.work` from your
beat schedule.

Needs `pip install isik[inbound]`.
"""
