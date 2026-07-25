# strings

Naming-convention converters for the usual places one crops up: env vars, model field names, display labels.

```python
from isik.common.utils.strings import camel_to_snake, snake_to_human, snake_to_pascal

camel_to_snake("HTTPResponseCode")  # "http_response_code"
snake_to_pascal("http_response")    # "HttpResponse"
snake_to_human("http_response")     # "Http Response"
```
