# REST views

`isik.django.apps.features.drf` - two viewsets to route, or ignore. Everything a holder can see or change
is there with its label, description and rules, in the request's language, so a client built from the
OpenAPI schema alone renders the features and the settings with no catalog of its own.

```python
router.register("features", FeatureViewSet, basename="feature")
router.register("settings", SettingViewSet, basename="setting")
```

Which holder a request is about is the `HOLDER` hook - `request.user` by default.

## `FeatureViewSet`

| route | |
|---|---|
| `GET /` | the holder's visible features: `{name, label, description, held, grants}` |

`held` is whether it's in force now; `grants` are those in force now or to come, each with its
`starts_at` and `expires_at` - and its `source` and `reference` only with `SHOW_GRANT_SOURCE`.

## `SettingViewSet`

| route | |
|---|---|
| `GET /` | the holder's visible settings; the response's `ETag` is their version |
| `PATCH /{name}/` | `{type, value}` - the holder's own value, `If-Match` naming the version read |

Each setting is `{name, type, label, description, value, default, chosen, origin, forced_reason, may_change,
requires_feature}`,
plus `choices: [{value, label}]` for a choice and `minimum`/`maximum` (from its field's validators) for a
number. The schema types it as a union discriminated on `type`, so a generated client narrows `value`;
a duration is `"HH:MM:SS"`, a decimal a string.

- **`origin`** - why it's that value: `forced`, `feature_missing`, `limited`, `own` or `default` (see
  [`effective()`](README.md#what-a-setting-is-effective)).
- **`forced_reason`** - the reason, where the force's source shows it to the holder; else null.
- **`chosen`** - the holder's own value, beside the one in force: what a release, or the feature, brings
  back. Null until they choose.
- **`requires_feature`** - the feature it needs, where the holder may see that feature; else null - so a
  locked setting names what unlocks it.
- **`may_change`** - false while forced or locked for want of a feature, and otherwise what
  `MAY_CHANGE_SETTING` says - so a client disables the control, and says why from `origin`.

### Writing one

One version covers all of a holder's settings - their overrides row's - so the list gives it once.

- **`If-Match` is required** (`428` without it); a stale one is `412`. `*` matches any.
- **Before the first write** there's no row, and the version is a stable "defaults" tag derived from the
  declarations. Naming it makes the row; of two first writes at once, the second is answered `412`.
- A `type` other than the setting's is `400`, and so is a value its field refuses (on `value`).
- A setting the holder can't see is `404`; one `may_change` refuses, `403`.
- A refusal the write itself meets is `409`, with its code: `setting_forced`, `setting_locked`.
- **A setting's own `request_policies`** - a fresh re-authentication, say - apply to its PATCH: their
  refusal is the policy's own `403`, with its headers, so the PATCH is the same gate as the project's own
  write path rather than a door past it.

The PATCH needs no `Idempotency-Key`: setting a value twice is setting it once. The schema says all of this -
`If-Match` required, the `ETag` header on the list and the PATCH, and each refusal's body - so a generated
client sends the version it read without being told. `value` and `default` are never null: `isik.E017`
refuses a setting whose field takes null or whose default is `None`.
