from functools import wraps
from itertools import chain

from django.utils.module_loading import import_string


def _first_segment(path):
    return path.split(".", 1)[0]  # pragma: no mutate


class ConditionalSerializerMixin:
    """
    Controls which fields show up based on `?include=`, `?only=`, and `?exclude=`.

    `?include=x,y` conditionally nests a relational field - declare it on `Meta.relational_fields`
    as a name -> zero-argument factory, see `relational_serializer` for the easy way to build one:

        class PersonSerializer(ConditionalSerializerMixin, ModelSerializer):
            class Meta:
                model = Person
                fields = ["id", "first_name", "last_name"]
                relational_fields = {"manager": relational_serializer("myapp.serializers.PersonSerializer")}

    `?only=x,y` narrows the response down to just those fields - an exhaustive allowlist.
    `?exclude=x,y` is the opposite, a denylist - it's applied last, so it always wins over
    `?include=`/`?only=`.

    All three take dotted paths reaching into a nested serializer, if it uses this mixin too -
    `?include=manager.manager` implies `?include=manager`, and `?only=manager.first_name` /
    `?exclude=manager.first_name` narrow/drop what `manager` itself shows. `manager` still has to
    actually be there for that to do anything, so reaching into a nested field needs `?include=`
    alongside, e.g. `?include=manager&only=manager.first_name`. Values can repeat (`?x=a&x=b`) or
    be comma-separated (`?x=a,b`).

    For a field built ad hoc in a `SerializerMethodField`, see `serializer_method_include`.
    """

    def get_fields(self):
        fields = super().get_fields()
        own_path = self._own_path()
        relational_fields = getattr(getattr(self, "Meta", None), "relational_fields", None)
        if relational_fields:
            requested = self._query_set("include")
            for name in relational_fields:
                full_path = f"{own_path}.{name}" if own_path else name
                if any(path == full_path or path.startswith(f"{full_path}.") for path in requested):
                    fields[name] = relational_fields[name]()
        keep = self._scoped_names("only")
        if keep:
            fields = {name: field for name, field in fields.items() if name in keep}
        drop = self._own_names("exclude")
        if drop:
            fields = {name: field for name, field in fields.items() if name not in drop}
        return fields

    def _own_path(self):
        override = getattr(self, "_path_override", None)
        if override is not None:
            return override
        segments = []
        node = self
        while node.parent is not None:
            segments.append(node.field_name)
            node = node.parent
        # many=True binds its child under an empty field_name, hence the filter.
        return ".".join(filter(None, reversed(segments)))

    def _scoped_names(self, key):
        """First segment past my own path, for values that reach me or go deeper - an ancestor
        stays visible even when only a deeper path (e.g. `only=owner.username`) mentions it."""
        own_path = self._own_path()
        prefix = f"{own_path}." if own_path else ""
        return {_first_segment(path[len(prefix) :]) for path in self._query_set(key) if path.startswith(prefix)}

    def _own_names(self, key):
        """Values that name one of my own fields exactly - unlike `_scoped_names`, a path
        reaching past me into a nested field doesn't count (it's that field's own to drop)."""
        own_path = self._own_path()
        prefix = f"{own_path}." if own_path else ""
        remainders = (path[len(prefix) :] for path in self._query_set(key) if path.startswith(prefix))
        return {remainder for remainder in remainders if "." not in remainder}

    def _query_set(self, key):
        query_params = getattr(self.context.get("request"), "GET", None)
        getlist = getattr(query_params, "getlist", None)
        if not callable(getlist):
            return set()
        return set(chain.from_iterable(raw.split(",") for raw in getlist(key)))


def relational_serializer(serializer, *args, **kwargs):
    """
    Builds a `relational_fields` factory, so you don't hand-write the `lambda:`.

    `serializer` can be the class itself, or an import path string - needed for
    self-reference, since a class can't name itself before its own body finishes running:

        relational_fields = {
            "manager": relational_serializer("myapp.serializers.PersonSerializer"),
            "reports": relational_serializer("myapp.serializers.PersonSerializer", many=True),
        }

    Defaults to `read_only=True`, since these are include-only fields.
    """

    def factory():
        cls = import_string(serializer) if isinstance(serializer, str) else serializer
        return cls(*args, **{"read_only": True, **kwargs})

    return factory


def serializer_method_include(getter):
    """
    Lets an ad hoc serializer built in a SerializerMethodField join dotted `?include=`/`?only=`/
    `?exclude=` paths, same as one declared through `relational_fields`. Parent must use
    ConditionalSerializerMixin.

    Return the serializer itself, not `.data` - this decorator calls that for you:

        owner_detail = serializers.SerializerMethodField()

        @serializer_method_include
        def get_owner_detail(self, obj):
            return OwnerSerializer(obj.owner, context=self.context)

    Field name comes from the method name, so only DRF's default `get_<field_name>`
    convention is supported. Returning `None` (e.g. a nullable relation) is passed through as-is.
    """
    field_name = getter.__name__.removeprefix("get_")

    @wraps(getter)
    def wrapper(self, obj):
        child = getter(self, obj)
        if child is None:
            return None
        parent_path = self._own_path()
        child._path_override = f"{parent_path}.{field_name}" if parent_path else field_name
        return child.data

    return wrapper
