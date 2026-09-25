# strings

Naming-convention converters for the usual places one crops up: env vars, model field names, display labels.

```python
from isik.common.utils.strings import camel_to_snake, snake_to_human, snake_to_pascal, words_to_pascal

camel_to_snake("HTTPResponseCode")  # "http_response_code"
snake_to_pascal("http_response")    # "HttpResponse"
snake_to_human("http_response")     # "Http Response"
words_to_pascal("profile.is_active")  # "ProfileIsActive"
words_to_pascal("IsSuperUser")        # "IsSuperUser" - snake_to_pascal would give "Issuperuser"
```

- `words_to_pascal` splits on every non-alphanumeric character and keeps each word's own inner capitals, so it takes dotted paths, snake_case and names that are already PascalCase alike. isik names its generated permission classes with it.
