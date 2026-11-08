"""
The SMS senders isik ships - each named by its dotted path in `ISIK['DJANGO']['SMS']`:

- `console.SMSBackend` - each message one line on stdout.
- `locmem.SMSBackend` - each message kept in `isik.django.sms.outbox`, for tests.
- `dummy.SMSBackend` - each message sent nowhere.
- `filebased.SMSBackend` - each message a JSON line in a file.
- `twilio.SMSBackend` - Twilio's Messages API (`sms` extra).
- `failover.SMSBackend` - the first of several senders that can be reached.
"""
