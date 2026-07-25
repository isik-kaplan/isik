# media_white_noise

`MediaWhiteNoiseMiddleware` serves `MEDIA_ROOT` files under `MEDIA_URL` via WhiteNoise, but only
when `settings.DEBUG` is true — for local/dev use so user-uploaded media works without a separate
static file server, never intended for production (where a real web server or storage backend
should serve media).

```python
MIDDLEWARE = [
    "isik.django.apps.common.middleware.media_white_noise.MediaWhiteNoiseMiddleware",
    ...,
]
```

- Falls through to `get_response` unconditionally when `DEBUG=False`, and also when the
  requested path doesn't resolve to a file even with `DEBUG=True`.
- Strips the current script prefix (`get_script_prefix()`) from `MEDIA_URL` before matching, so it
  still works correctly behind a path-prefixed deployment.
