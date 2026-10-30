# Channels

A delivery goes out on one channel. Each says who a notice reaches on it and sends one delivery. A
delivery's target lives in the channel's own child row (`InboxDelivery`, `EmailDelivery`,
`WebhookDelivery`, `PushDelivery`), never in a nullable column of `Delivery`.

## inbox

One `InboxItem` per user, keyed by its delivery - so it lands once however often it's tried. Its
`payload` is `{"data": <snapshot>, "previous": <previous>}`.

It carries who acted - the notice's `actor_id` and `context`, copied - for "Ada archived the
organization". `InboxViewSet` never shows either: a staff member's id isn't the recipient's to read. Show
your own label, resolved by your own rules, in your own serializer.

### Who acted

Every notice records who acted, however it was written - by its trigger, `raise_notice()` or
`record_email()`: `actor_id` is `ACTOR_CONTEXT_KEY` (`"user"`, which isik's `HistoryContextMiddleware` and
`HistoryContextTask` set) of the history context it was written in, as text; null when nobody acted - a
migration, a command, a sweep. `context` keeps the context's `NOTICE_CONTEXT_KEYS` beside it: whatever
it takes to read `actor_id` back where a primary key alone names a different person in each partition -
the schema the actor lives in, say:

```python
ISIK_NOTIFICATIONS = {"NOTICE_CONTEXT_KEYS": ("user", "schema")}
```

Both are read where the notice is written, so changing either setting changes the trigger SQL - a
migration, like any other change to a kind. Neither reaches a webhook's payload: a user id is the
project's to send a third party, in its own payload.

## email

Sent by `EMAIL_SENDER(delivery, notice, address)`, inside the delivery's partition. Two senders ship:

- `send_from_templates` (the default) renders `notifications/<kind>/subject.txt` and `body.txt` - and
  `body.html` when it exists - with `notice`, `delivery`, `data` and `previous`, over what
  `EMAIL_CONTEXT(delivery, notice)` gives every email (the site's name, say).
- `send_from_mjml_templates` is the same, its HTML from `notifications/<kind>/body.mjml` through isik's
  MJML rendering.

Both send through Django's mail with a `Message-ID` taken from the delivery's id, and both:

- render the plain-text templates with **no HTML escaping** - an apostrophe in a subject is an apostrophe -
  and `body.html` escaped, as HTML;
- need Django's template backend, which `isik.E014` checks while one of them is the sender, along with
  each email kind's `subject.txt` and `body.txt`.

Every sender - these, or yours - runs in the recipient's language: `RECIPIENT_LANGUAGE(user)`, by default
the user's `language` when it's set to something - an empty one means no choice was made, and the site's
language answers.

A template missing at send time fails the delivery for good - sending again finds the same nothing.
Point `EMAIL_SENDER` at your own sending path instead (isik never installs an `EMAIL_BACKEND`).
`EMAIL_ADDRESS(user)` picks the address - the user model's email field by default - and a user without
one gets no email.

## webhook

Every active, unparked endpoint whose `EndpointKind` rows cover the notice's kind and variant gets a
`POST` - every one of them, unless endpoints are owned.

**Owned endpoints.** When each endpoint is someone's - a user's, an organization's - set
`ENDPOINT_OWNER_FIELD` to the endpoint's lookup naming its owner's users (`"owner"`,
`"organization__members"`). An endpoint then gets only the notices whose audience holds its owner - before
any subscription: its `EndpointKind` rows are its own choice. Leave it unset only when every endpoint is the
deployment's, or each owner's endpoint is sent every other owner's notices. A partition doesn't need it: an
endpoint there hears only its partition's notices already. `isik.E014` refuses an `EndpointViewSet` or
`DeliveryViewSet` scoped by an `owner_field` that isn't `ENDPOINT_OWNER_FIELD`, and `isik.W008` warns of an
endpoint model keyed to the user model while it's unset - an owner key added to the model, and never to the
setting. A key that isn't ownership (who registered it, say) is said so with a reason:
`ENDPOINTS_SHARED=SharedEndpoints(reason=...)`, from `isik.django.apps.notifications.exemptions`.

```http
POST /your/hook HTTP/1.1
Content-Type: application/json
Idempotency-Key: 6f1c...            (the delivery's id - the same on every attempt)
X-Acme-Signature: t=1761600000,v1=5d2a...[,v1=<by the previous secret>]

{"id": "6f1c...", "kind": "parcel_flagged", "variant": "lost", "occurred_at": "...",
 "subject": {"label": "shop.Parcel", "id": "42", "partition": null},
 "data": {...snapshot...}, "previous": null}
```

**Signing.** `v1` is the hex HMAC-SHA256, by the endpoint's secret, of `"<t>.<raw body>"`. While a
secret is being rotated out (`secret_previous` set), a second `v1=` signs with it. A receiver checks
each `v1=` against its secret and accepts the request if any matches. **Don't parse the header into a
dict** - that keeps one `v1` and refuses half of the deliveries during a rotation. Check `t` against
your clock to refuse old replays.

**The header's name** is `X-<PROJECT_NAME>-Signature`, or `SIGNATURE["header"]` outright. `PROJECT_NAME`
is needed only when some kind is sent by webhook, and must be an HTTP token. It's a wire contract every
receiver is built against, not a label: never derive it from a display or branding name that can change.

**Receivers dedupe on `Idempotency-Key`.** A webhook is delivered at least once - see the README.

**Where a webhook may go.** An endpoint is a URL someone gives, so by default a webhook only ever reaches
a **public** address: loopback, private, link-local and every other non-global address is refused -
your database's host, a cloud metadata service (169.254.169.254, 100.100.100.200), the workers' own
network. The address is checked where it's connected to - the host is resolved once, that address
checked and connected to, so a name that resolves somewhere public when checked and somewhere private a
moment later gets nowhere. Redirects aren't followed, the environment's proxy settings aren't used, and
only http and https URLs are accepted.

A refused endpoint fails for good, its reason naming what kind of address it was - never the address,
which would map your network for whoever registers endpoints; the operator's log has it.
`ALLOW_PRIVATE_TARGETS` opens it up: `True`, or the networks a webhook may reach (`["10.1.0.0/16"]`) -
for an internal receiver, or development. `WEBHOOK_PROXY` sends them out through an egress proxy; then
isik can check only the address it resolves itself, since the proxy resolves again, so the proxy has to
keep its own egress to what it should reach.

**How much it waits.** A response is read up to `MAX_RESPONSE_BYTES`, and the whole answer has
`ENDPOINT_TIMEOUT` seconds - an endpoint sending a body without end, or a byte at a time, holds a worker
no longer than that.

**What's tried again:** the statuses in `RETRYABLE_STATUSES` - 5xx, 408, 425 and 429 by default - a
timeout, and a refused connection. Any other status is the receiver refusing the request itself, and the
delivery is abandoned. An endpoint that's gone, inactive or parked abandons its deliveries unsent.

**Parking.** `DISABLE_AFTER` failures in a row park an endpoint - nothing is sent to it until
`endpoint.unpark()`, which also clears its count - and send the `endpoint_parked` signal
(`isik.django.apps.notifications.signals`), once, with `endpoint`, so you can tell its owners with a kind
of your own. A success clears the count.

**Secrets.** `Endpoint.secret` is a plain field on the base; override it (and `secret_previous`) with an
encrypted field of your own.

## push

`pip install isik[push]`, then add `"push"` to `CHANNELS`. Each active device of each user a notice
reaches gets a delivery of its own, sent straight to its platform's service over HTTP/2 - APNs for an iOS
token, FCM for a Firebase one. No middleman, no SDK: the extra is `httpx[http2]` and `pyjwt[crypto]`.

```python
ISIK_NOTIFICATIONS = {
    "CHANNELS": ("inbox", "email", "push"),
    "PUSH": {
        "APNS": {"TEAM_ID": "...", "KEY_ID": "...", "KEY": os.environ["APNS_KEY"], "TOPIC": "com.acme.app"},
        "FCM": {"CREDENTIALS": json.loads(os.environ["FCM_SERVICE_ACCOUNT"])},
    },
}
```

Either service, or both. `KEY` is the `.p8` key's PEM; `TOPIC` the app's bundle id, which a device's own
`app_id` overrides. `CREDENTIALS` is the Firebase service account's JSON (parsed, or as a string), its
project named by `project_id` unless `PROJECT_ID` says otherwise. A kind sent on push with neither set is
refused at startup.

**Devices.** `Device` (in the audience app, swappable as `ISIK_NOTIFICATIONS_DEVICE_MODEL`) is one app
install: `platform` (`apns` or `fcm`), `token`, `environment` (`production`, or `sandbox` for a development
build's APNs token - the two hosts refuse each other's), `app_id`, `is_active` and `last_seen_at`. A token
is unique on its platform: `register_device(user, platform, token, environment=..., app_id=...)` makes the
device, or - registered before, by anyone - moves it to `user` and wakes it, since whoever registers a token
is signed in on that phone now. Call it whenever the app starts or its token changes, or route
[`DeviceViewSet`](rest.md#deviceviewset). `forget_devices(user)` forgets them all - at a sign-out
everywhere, say. A user with no device gets no push; the inbox is the record.

**What a push says.** The kind's `title`, else its `label`, over its `message` - in the recipient's language
(`RECIPIENT_LANGUAGE`). Its data, for the app to open the right screen: `notice`, `kind`, `variant`,
`subject_label` and `subject_id`, each as text. The kind's `push=PushContent(...)` says the rest
([kinds.md](kinds.md#a-kinds-push)): `private=True` for the label alone, `data=` for snapshot fields beside
those - each with its reason - `collapse=`, `ttl=` and `badge=True`, the user's unread inbox count.

**A dead token.** APNs answering 410, `BadDeviceToken`, `DeviceTokenNotForTopic` or `Unregistered`, or FCM
`UNREGISTERED` or an `INVALID_ARGUMENT` on the token, deactivates the device: its delivery is abandoned,
and it gets no more until the app registers it again. Nothing else deactivates one - a refusal of isik's
credentials (`InvalidProviderToken`, `SENDER_ID_MISMATCH`, Google refusing the service account) is
permanent for the delivery and leaves every device be, since it's yours to fix, not the phone's. An expired
token, isik's own, is made again on the next try. A busy service (`RETRYABLE_STATUSES`) is tried again.

**Credentials kept.** One HTTP/2 client a worker - APNs wants its connection reused - and one token per
service: APNs's provider token for 50 minutes (Apple refuses one older than an hour, or made afresh more
often than every twenty minutes), FCM's access token until five minutes before Google says it expires. A
push takes `ENDPOINT_TIMEOUT` at most. Its attempt records the request without its credentials, and its
`apns-id` is the delivery's, so a support question finds it.

## Your own

```python
from isik.django.apps.notifications.channels import Channel, Outcome, Target, register_channel

class SmsChannel(Channel):
    def targets(self, notice, kind, users):
        return [Target(f"sms:{user.pk}", SmsDelivery, {"phone": user.phone}) for user in users if user.phone]

    def send(self, delivery):
        ...
        return Outcome(succeeded=True)   # or Outcome(succeeded=False, permanent=True, error="...")

register_channel("sms", SmsChannel)   # or its dotted path
```

Add it to `CHANNELS`. `targets()` runs inside the partition; each `Target` names its key (unique per
notice) and its child row. Raising from `send()` counts as a failure to try again. `attempted(delivery,
outcome)` runs after each attempt, and `unretriable(delivery)` says why a delivery can't be retried by
hand, or None.
