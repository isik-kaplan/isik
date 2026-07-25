# mixin

`SkippableValidatorsMixin` wraps every validator on every local field/M2M of a subclass with
`make_skippable`, once Django finishes preparing the class (on the `class_prepared` signal — `_meta`
isn't ready any earlier). It also adds `skip_field_validators`/`skip_named_validators` instance
methods as shortcuts for `SkipFieldValidators`/`SkipNamedValidators`.

```python
from isik.django.apps.common.skippable_validators import SkippableValidatorsMixin

class Widget(SkippableValidatorsMixin, models.Model):
    count = models.IntegerField(validators=[positive_only])

widget = Widget(count=-1)
with widget.skip_field_validators("count"):
    widget.full_clean()
```

- `BaseModel` already includes this mixin — only needed directly on models that don't go through
  `BaseModel`.
- Validators already marked skippable (`_is_skippable`) are left alone, so this is safe on a
  subclass that redeclares fields already wrapped elsewhere.
