"""`segments()` - how many parts a body is sent as, and in which encoding: what a provider bills by."""

from dataclasses import dataclass


# The GSM 03.38 alphabet: one septet each - and the extension table's, two (an escape, then the character).
GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM_EXTENSION = set("\f^{}\\[~]|€")
# {encoding: (characters in one part, in each part of several)} - a split message spends some on a header.
LIMITS = {"GSM-7": (160, 153), "UCS-2": (70, 67)}


@dataclass(frozen=True)
class Segments:
    """A body's `encoding` - `"GSM-7"` or `"UCS-2"` - its length in that encoding's units, and the parts it takes."""

    encoding: str
    units: int
    count: int


def segments(body):
    """
    How `body` is sent: GSM-7 while every character is in its alphabet - 160 to a part, 153 to each of several,
    an extension character (`€`, `{`...) counting twice - else UCS-2 for the whole body, 70 to a part and 67 to
    each of several, a character outside the basic plane (most emoji) counting twice.

        segments("Your code is 123456")    # Segments(encoding="GSM-7", units=19, count=1)
    """
    if all(character in GSM_BASIC or character in GSM_EXTENSION for character in body):
        encoding = "GSM-7"
        units = sum(2 if character in GSM_EXTENSION else 1 for character in body)
    else:
        encoding = "UCS-2"
        units = len(body.encode("utf-16-le")) // 2
    single, each = LIMITS[encoding]
    count = 1 if units <= single else -(-units // each)
    return Segments(encoding, units, count)
