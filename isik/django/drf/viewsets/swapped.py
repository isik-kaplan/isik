"""`SwappedModel` - a viewset's `model`, read from the getter of a swappable model."""


class SwappedModel:
    """
    A viewset's `model`, read from its getter whenever it's asked for - the model a project swapped in, not
    the one isik ships - so isik's checks and schema generation find it on the class, with no request.
    """

    def __init__(self, getter):
        self.getter = getter

    def __get__(self, instance, owner):
        return self.getter()
