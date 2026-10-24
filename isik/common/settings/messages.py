"""
Every message a setting's resolution can show, named by `Message` so a `Settings` class can word any of
them its own way - `messages={Message.REQUIRED: "%(name)s is needed"}`. Placeholders are `%(name)s`.
"""

from enum import Enum

from isik._internal.translation import gettext as _


class Message(Enum):
    REQUIRED = "required"
    EMPTY = "empty"
    INVALID = "invalid"
    INVALID_SECRET = "invalid-secret"
    BELOW_MIN = "below-min"
    ABOVE_MAX = "above-max"
    ABOVE_SOFT_MAX = "above-soft-max"
    SOFT_OVERRIDE = "soft-override"
    SOFT_HEADER = "soft-header"
    SOFT_CONFIRM = "soft-confirm"
    BOTH_GIVEN = "both-given"
    REQUIRES = "requires"
    EXCLUDES = "excludes"
    NOT_APPLICABLE = "not-applicable"
    ASKED_ERROR = "asked-error"
    NOT_CONFIRMED = "not-confirmed"
    NO_HOME = "no-home"


def default_template(message):
    # Built on each call, so each is translated in the language active when it's shown.
    return {
        Message.REQUIRED: _("%(name)s is required"),
        Message.EMPTY: _("%(name)s must not be empty"),
        Message.INVALID: _("%(name)s: %(error)s, got %(value)r"),
        Message.INVALID_SECRET: _("%(name)s is not valid"),
        Message.BELOW_MIN: _("%(name)s must be at least %(min)s"),
        Message.ABOVE_MAX: _("%(name)s must be at most %(max)s"),
        Message.ABOVE_SOFT_MAX: _(
            "%(name)s is %(value)s, above the recommended safe limit of %(soft_max)s - this may take a long time"
        ),
        Message.SOFT_OVERRIDE: _("%(message)s - pass %(override)s to override"),
        Message.SOFT_HEADER: _("These may take a long time:"),
        Message.SOFT_CONFIRM: _("Continue anyway?"),
        Message.BOTH_GIVEN: _("%(name)s and %(other)s can't both be given"),
        Message.REQUIRES: _("%(name)s needs %(other)s"),
        Message.EXCLUDES: _("%(name)s can't be used with %(other)s"),
        Message.NOT_APPLICABLE: _("%(name)s can't be used here"),
        Message.ASKED_ERROR: _("Error: %(message)s"),
        Message.NOT_CONFIRMED: _("The two entries don't match - try again."),
        # %(variable)s too: a default came from nowhere, so a server's message can still name its variable.
        Message.NO_HOME: _("%(name)s: there's no home directory to expand ~ in - set it to a full path"),
    }[message]
