class Sentinel:
    """
    Creates a sentinel object that ever only equals to itself.
    Sentinels are unique by name — calling Sentinel("FOO") twice returns the same instance.

    Example:
        Sentinel("FOO") is Sentinel("FOO")  # True
    """

    _registry = {}

    def __new__(cls, name):
        if name in cls._registry:
            return cls._registry[name]
        instance = super().__new__(cls)
        cls._registry[name] = instance
        return instance

    def __init__(self, name):
        """
        Name should preferably follow the constant name convention like `SENTINEL_NAME`.
        """
        self.name = name

    def __repr__(self):
        return f"<Sentinel:{self.name}>"

    def __str__(self):
        return f"<Sentinel:{self.name}>"

    def __bool__(self):
        return False

    def __eq__(self, other):
        return self is other

    def __hash__(self):
        return id(self)
