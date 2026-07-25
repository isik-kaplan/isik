from itertools import combinations


def not_none(value):
    """Returns if the value is not None, mostly used as a predicate"""
    return value is not None


def first_of(iterable, default=None, pred=None):
    """
    Returns the first true value in the iterable.

    If no true value is found, returns default.
    If pred is not None, returns the first item for which pred(item) is true.

    Example:
        first_of([None, None, "x"])  # "x"
        first_of([a, b], default=c, pred=not_none)  # a if a is not None else b if b is not None else c
    """
    return next(filter(pred, iterable), default)


def purge_iterable(iterable, items):
    """
    Remove items from an iterable
    """
    items = set(items)
    return [item for item in iterable if item not in items]


def purge_mapping(mapping, keys):
    """
    Remove keys from a mapping
    """
    return {key: value for key, value in mapping.items() if key not in keys}


def all_combinations(options):
    """
    Returns all possible combinations of the given options
    """
    return [list(comb) for r in range(1, len(options) + 1) for comb in combinations(options, r)]
