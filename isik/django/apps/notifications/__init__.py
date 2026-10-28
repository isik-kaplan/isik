"""
`isik.django.apps.notifications` - notices written by the database inside the transaction that made the
change, fanned out into deliveries on the inbox, email and webhook channels, each tried until it's sent.

Two apps, installed together: this one, the ledger (notices, deliveries, attempts - nothing naming a
person), and `isik.django.apps.notifications.audience`, the rows that do (subscriptions, inbox items,
endpoints). Declare a kind with `notifies()` or `declare_kind()` (in `kinds`), configure with
`ISIK_NOTIFICATIONS` (see `conf`), and run the tasks in `tasks` from your beat schedule.

Needs `pip install isik[notifications]`.
"""
