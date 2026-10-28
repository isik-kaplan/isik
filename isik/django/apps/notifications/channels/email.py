"""
Email, sent by `EMAIL_SENDER` inside the delivery's partition - a project's own mail path, or one of the
two senders here, rendering templates named by the kind:

- `send_from_templates` (the default) - `notifications/<kind>/subject.txt` and `body.txt`, and `body.html`
  when there is one.
- `send_from_mjml_templates` - the same, its HTML from `body.mjml` through isik's MJML rendering.

Every sender - these or a project's own - runs in the recipient's language (`RECIPIENT_LANGUAGE`). These
two start each email's context from `EMAIL_CONTEXT`, and render the plain-text templates with no HTML
escaping - an apostrophe in a subject is an apostrophe, not `&#x27;`. Both need Django's own template
backend, which a check holds them to. A template that doesn't exist fails the delivery for good: sending it
again finds the same nothing.
"""

from contextlib import nullcontext

from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.core.mail.utils import DNS_NAME
from django.template import Context, TemplateDoesNotExist
from django.template.loader import get_template, render_to_string
from django.utils import translation

from isik.django.apps.notifications import conf
from isik.django.apps.notifications.channels import Channel, Outcome, Target
from isik.django.apps.notifications.partitions import in_partition
from isik.django.apps.notifications.swapping import get_email_delivery_model


def address_of(user):
    """The address `user` is emailed at: `EMAIL_ADDRESS(user)`, else the user model's email field."""
    hook = conf.setting("EMAIL_ADDRESS")
    if hook is not None:
        return hook(user)
    return getattr(user, user.get_email_field_name()) or None


def language_of(user):
    """The default `RECIPIENT_LANGUAGE`: `user.language` when it's set to something, else None - an empty one
    is a choice not made, which the site's own language answers."""
    return getattr(user, "language", None) or None


def _language(delivery):
    """The language the delivery's email is written in - its user's, when it has one - or None."""
    # An address belonging to nobody - user_id null - finds no user, and is written in the site's language.
    user = get_user_model()._default_manager.filter(pk=delivery.email.user_id).first()
    return conf.setting("RECIPIENT_LANGUAGE")(user) if user is not None else None


# A plain-text part is text, not HTML: an apostrophe in it is an apostrophe, not &#x27;.
PLAIN_TEXT = {"autoescape": False}


def plain(name, context):
    """Template `name` rendered with no HTML escaping, for a plain-text part."""
    return get_template(name).template.render(Context(context, **PLAIN_TEXT))


def _send(delivery, notice, address, html):
    shared = conf.setting("EMAIL_CONTEXT")
    context = {
        **(shared(delivery, notice) if shared is not None else {}),
        "notice": notice,
        "delivery": delivery,
        "data": notice.snapshot,
        "previous": notice.previous,
    }
    base = f"notifications/{notice.kind_id}"
    message = EmailMultiAlternatives(
        " ".join(plain(f"{base}/subject.txt", context).split()),
        plain(f"{base}/body.txt", context),
        to=[address],
        headers={"Message-ID": f"<{delivery.id}@{DNS_NAME}>"},
    )
    try:
        message.attach_alternative(html(base, context), "text/html")
    except TemplateDoesNotExist:
        pass
    message.send()


def send_from_templates(delivery, notice, address):
    """
    The default `EMAIL_SENDER`: renders `notifications/<kind>/subject.txt` and `body.txt` - and `body.html`,
    when there is one - with `EMAIL_CONTEXT`'s, then `notice`, `delivery`, `data` (the snapshot) and
    `previous`, and sends them
    through Django's mail. Its Message-ID is the delivery's, the same on every attempt, so a receiving
    server can drop a second copy.
    """
    _send(delivery, notice, address, lambda base, context: render_to_string(f"{base}/body.html", context))


def send_from_mjml_templates(delivery, notice, address):
    """`send_from_templates`, its HTML part from `notifications/<kind>/body.mjml`, through isik's MJML."""
    from isik.django.apps.common.email.templates import mjml_template

    _send(delivery, notice, address, lambda base, context: mjml_template(f"{base}/body.mjml", context))


class EmailChannel(Channel):
    """Email, sent by `EMAIL_SENDER` inside the delivery's partition."""

    name = "email"

    def targets(self, notice, kind, users):
        targets = []
        for user in users:
            address = address_of(user)
            if address:
                targets.append(
                    Target(
                        f"email:{address.lower()}",
                        get_email_delivery_model(),
                        {"address": address, "user_id": str(user.pk)},
                    )
                )
        return targets

    def send(self, delivery):
        request = {"to": delivery.email.address}
        try:
            with in_partition(delivery.partition):
                language = _language(delivery)
                with translation.override(language) if language else nullcontext():
                    conf.setting("EMAIL_SENDER")(delivery, delivery.notice, delivery.email.address)
        except TemplateDoesNotExist as missing:
            return Outcome(succeeded=False, permanent=True, request=request, error=f"No template {missing}.")
        return Outcome(succeeded=True, request=request)
