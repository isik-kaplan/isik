"""
The SMS signals - each sent by the sender's class, with `sender_alias` its alias:

- `pre_send(message)` - before a message is sent. A receiver may change it, or raise `SMSCancelSend` to send
  it nowhere.
- `post_send(message, status)` - after, with what the provider answered (`message.send_status`).
- `delivery(event)` - a delivery report a provider's webhook posted, a `DeliveryEvent`.
- `inbound(event)` - a message someone sent the project's number, an `InboundEvent` - a STOP among them.
"""

from django.dispatch import Signal


pre_send = Signal()
post_send = Signal()
delivery = Signal()
inbound = Signal()
