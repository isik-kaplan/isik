from functools import partial

import sentry_sdk

from isik.common.utils import SuppressAndRun, suppress_callable


suppress_to_sentry = partial(SuppressAndRun, func=sentry_sdk.capture_exception)
suppress_callable_to_sentry = partial(suppress_callable, func=sentry_sdk.capture_exception)
