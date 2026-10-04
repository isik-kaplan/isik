"""What a write changes - shared by the field guards and create-only fields, which both ask it."""

MISSING = object()


def dig(value, attrs):
    for attr in attrs:
        if isinstance(value, dict):
            value = value.get(attr, MISSING)
        else:
            value = getattr(value, attr, MISSING)
        if value is MISSING:
            return MISSING
    return value


def changed(stored, incoming):
    if hasattr(stored, "all"):  # a related manager - compare the rows, not the manager
        return set(stored.all()) != set(incoming)
    if isinstance(incoming, dict):  # a nested serializer's payload - no stored value to compare it to
        return True
    return stored != incoming
