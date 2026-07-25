# current_user

`CurrentUserField` (a `HiddenField`) enforces that a field always resolves to the request's authenticated user - a client can never supply or override it, unlike pairing a plain default with a writable field.

```python
owner = CurrentUserField()
created_by = CurrentUserField(create_only=True)  # keeps the instance's existing owner on update
```

- `create_only=False` (the default) re-resolves to whoever is making the request on *every* save, including updates - not just at creation. Use `create_only=True` to pin it to whatever the instance already had once it exists.
- `CurrentUser` (the underlying `default=`) is exposed separately too, but pairing it with an ordinary writable field doesn't stop a client from overriding the value - `HiddenField.get_value()` ignoring input entirely is what actually enforces it.
