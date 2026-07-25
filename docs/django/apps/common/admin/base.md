# base

`BaseAdmin` is a `ModelAdmin` base (also mixing in `django-object-actions` and DALF autocomplete
filtering) that layers isik's own conventions on top: readonly fields and list display
automatically pick up `BaseModel.FIELDS` (`id`, `created_at`, `updated_at`), fieldsets split into
"object" vs "meta" sections, and specific fields can be force-set to `request.user` on save.

```python
from isik.django.apps.common.admin import BaseAdmin

class WidgetAdmin(BaseAdmin):
    search_fields = ["name"]
    autocomplete_list_filter = ["owner"]
    object_fieldsets = [[["name", "count"], "Widget"]]
    create_force_field_as_current_user = ["owner"]
```

- `safe_m2m_fields`: Django's admin hides a `ManyToManyField` whose `through` model isn't
  `auto_created` (e.g. it carries extra fields). Listing a field name here temporarily flips
  `auto_created = True` on the through model while rendering that form field, then flips it back.
- `excluded_list_display` defaults to `["slug"]` — the rest of `BaseModel.FIELDS` always appends
  to `list_display`/readonly fields regardless of what you set on the subclass.
- `*_force_field_as_current_user` (`create_`/`update_`/`global_`) run in `save_model`, after the
  form's own value is already applied to the instance — they overwrite unconditionally, they
  don't just supply a default.
