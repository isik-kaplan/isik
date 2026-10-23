# flattened_singleton_related

`FlattenedSingletonRelatedMixin` - one row among a parent's many related ones, exposed as a field of
the parent, read from it and written through to it: a site's primary domain among all its domains.

```python
class SiteDomain(BaseModel):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="domains")
    domain = models.CharField(max_length=100)
    is_primary = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site"], condition=Q(is_primary=True), name="one_primary_domain"),
        ]


class SiteSerializer(FlattenedSingletonRelatedMixin, BaseModelSerializer):
    class Meta:
        model = Site
        fields = ["id", "name", "primary_domain"]
        flattened_singleton_related_fields = {
            "primary_domain": SingletonRelated("domains", {"is_primary": True}, "domain"),
        }

# GET   -> {"id": ..., "name": "example", "primary_domain": "example.com"}   (null when there's no such row)
# PATCH {"primary_domain": "example.org"}   -> updates that row's domain, or creates the row
# PATCH {"primary_domain": null}            -> deletes the row (on_empty="delete", the default)
```

`SingletonRelated(accessor, match, attribute, on_empty="delete")`:

- `accessor` - the reverse foreign key the parent reaches its rows by.
- `match` - which row is the one: equality on the related model's own concrete fields, never the
  key back to the parent (the manager supplies it). It is also what a missing row is created with,
  so a lookup (`domain__startswith`), a callable or an expression in it raises
  `ImproperlyConfigured` at once.
- `attribute` - what of that row the field shows and writes. The field is built from the related
  model's own field, so its validation (a `max_length`, choices) applies.
- `on_empty` - what an empty value (`None` or `""`) does: `"delete"` the row - it exists only to
  carry this value - `"clear"` the attribute (which must be nullable), or `"refuse"` the write.

## Reading

The matching row's `attribute`, or `None` when there's no such row - present and null. The field is
listed in `Meta.fields` like any other (or published by `"__all__"`), and absent where it isn't: null
says "no such row", absent says "this surface doesn't publish it".

## Writing

On create and update alike, inside the save's transaction:

- A matching row is locked (`select_for_update`), set, and saved with `update_fields=[attribute]` -
  nothing else of a row the serializer didn't load for this is written back.
- No matching row: one is created from `match` and the value. A concurrent request creating it too
  loses on the unique constraint, and gets a 400 naming the field - not a 500.
- A Django `ValidationError` from the row's own `full_clean()` comes back as a 400.

## Proven, not assumed - `isik.E006`

"The one" holds only while the database says so. The system check `isik.E006`
([checks](../../apps/common/checks.md)) refuses a declaration unless the related model has a partial
`UniqueConstraint` over its key to the parent whose condition is exactly `match` - and an accessor
that isn't a reverse foreign key, a `match` or `attribute` that isn't a concrete field of the related
model, or `on_empty="clear"` on a non-nullable attribute. `singleton_related_problems()` lists them:

```
SiteSerializer.primary_domain: nothing proves testapp.SiteDomain has one row per testapp.Site matching
{'is_primary': True} - add UniqueConstraint(fields=['site'], condition=Q(**{'is_primary': True})).
```
