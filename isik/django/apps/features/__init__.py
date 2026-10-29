"""
`isik.django.apps.features` - features granted to a holder, and the settings they unlock, which staff can force.

Declare features and settings in code (`declarations`), grant features and force settings (`operations`), and read a
setting's value - and where it came from - with `resolution.effective()`. The holder is
`ISIK_FEATURES_HOLDER_MODEL`: the user model unless set. The settings' columns live on the project's own overrides
model (`bases.AbstractOverrides`); configure the rest with `ISIK_FEATURES` (see `conf`).
"""
