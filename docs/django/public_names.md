# public names

A name isik puts where someone outside the project's code sees it - an HTTP header on the wire - is the project's
to choose: each is a setting of `ISIK['DJANGO']`, with a default, and none is named after isik.

| setting | default | seen as |
|---|---|---|
| `ISIK['DJANGO']['SYNC']['PROTOCOL_HEADER']` | `Sync-Protocol` | the header every sync request and response names its protocol version in - `@isikk/core`'s `protocolHeader` the same |
| `ISIK['DJANGO']['HISTORY']['REQUEST_ID_HEADER']` | `X-Request-ID` | the header a request's correlation id is read from, and answered under |
| `ISIK['DJANGO']['SESSIONS']['DEAD_SESSION_HEADER']` | `Session-Expired` | the header saying a request's session cookie is dead |

`isik.django.public_names` holds them - `public_names()` lists those of what the project uses (an app installed, a
middleware in `MIDDLEWARE`), and each `PublicName` has its `setting`, `value()`, `default` and `overridden()`. The
code using a name reads it there, so the list is every one.

```console
$ python manage.py public_names              # those of what the project uses
$ python manage.py public_names --defaults   # only those left at isik's default
$ python manage.py public_names --all        # every one, in use or not
$ python manage.py public_names --format json
$ isik check public_names --defaults         # the same, from the isik command
```

A client's own names - `@isikk/core`'s sync database, its channel and lock, its headers - are listed by
`syncPublicNames()` there.
