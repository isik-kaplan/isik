# context

`make_skippable(validator, field_name, name=None)` wraps a field validator so it becomes a no-op
whenever the current field or validator name is in the active skip set. `SkipFieldValidators(*field_names)`
and `SkipNamedValidators(*names)` are context managers (also usable as decorators, being
`ContextDecorator`) that add to that skip set for their duration, backed by a `ContextLocal` so
it's safe across threads/async tasks.

```python
from isik.django.apps.common.skippable_validators import SkipNamedValidators

with SkipNamedValidators("positive_only"):
    widget.full_clean()
```

- Skip sets accumulate per context (`current | set(...)`) and reset via a token on exit — nested
  `with` blocks compose instead of clobbering each other.
- You normally don't call `make_skippable` directly — `SkippableValidatorsMixin` (in `mixin.py`)
  wraps every field validator on a model automatically.
