"""
What the features app tells a project - neither reaches the holder's people itself; a receiver doing so is the
project's, entering their partition through its own guard:

- `setting_changed(holder, name, before, after)` - a setting's resolved value moved, whatever moved it: a force,
  a release, the holder's own write, a feature granted, revoked, started or ended. `before`/`after` are
  `Effective`s.
- `feature_changed(holder, feature, held)` - a feature came into force for a holder, or went out of it.
"""

from django.dispatch import Signal


setting_changed = Signal()
feature_changed = Signal()
