def get_cached(obj, attr, factory):
    """
    Get a cached attribute from an object, computing and storing it if absent.

    Bypasses __getattribute__ via object.__getattribute__ and object.__setattr__,
    making it safe to call from within a custom __getattribute__ implementation.

    Args:
        obj: The object to cache the attribute on.
        attr: The attribute name to use as the cache key.
        factory: A zero-argument callable that computes the value on cache miss.

    Returns:
        The cached value if present, otherwise the result of calling factory().
    """
    try:
        return object.__getattribute__(obj, attr)
    except AttributeError:
        value = factory()
        object.__setattr__(obj, attr, value)
        return value
