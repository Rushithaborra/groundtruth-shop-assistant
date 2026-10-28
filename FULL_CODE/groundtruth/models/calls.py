"""Every call to a provided model goes through `call_model`: a timeout, a retry with back-off, and a log record.

A model that is slow, fails, or returns nothing usable must never take the assistant down; the caller falls back
to the rule-based answer when `call_model` raises ModelCallError.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")

# Errors a local model can raise on one call. A native-library or file failure may be temporary, so it is retried;
# bad input (ValueError, e.g. a prompt longer than the context window) fails the same way every time, so it isn't.
# Anything else is a programming error and is allowed to surface.
RETRYABLE_ERRORS = (RuntimeError, OSError)

# One worker per process: local models aren't thread-safe, and a timed-out call keeps running until it finishes.
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model-call")
_running: list[Future] = []  # the call in the worker, if any; a new call doesn't queue behind a timed-out one


class ModelCallError(RuntimeError):
    """A model call timed out or failed on every attempt."""


def call_model(fn: Callable[[], T], *, what: str, timeout_s: float, retries: int, backoff_s: float) -> T:
    """Run `fn` with a timeout, retrying failures with exponential back-off; raise ModelCallError when all fail."""
    if _running and not _running[0].done():
        logger.warning("%s skipped: an earlier model call is still running; using the rule-based answer", what)
        raise ModelCallError(f"{what} skipped: model busy")
    for attempt in range(retries + 1):
        start = time.perf_counter()
        future = _EXECUTOR.submit(fn)
        _running[:] = [future]
        try:
            result = future.result(timeout=timeout_s)
        except FutureTimeout:
            # a timed-out call keeps the worker busy, so retrying would only queue behind it
            logger.warning("%s timed out after %.1f s; using the rule-based answer", what, timeout_s)
            raise ModelCallError(f"{what} timed out") from None
        except ValueError as exc:
            logger.warning("%s rejected its input: %s; using the rule-based answer", what, exc)
            raise ModelCallError(f"{what} rejected its input: {exc}") from exc
        except RETRYABLE_ERRORS as exc:
            logger.warning("%s failed (attempt %d of %d): %s", what, attempt + 1, retries + 1, exc)
            if attempt < retries:
                time.sleep(backoff_s * (2**attempt))
            continue
        logger.debug("%s took %.0f ms", what, (time.perf_counter() - start) * 1000)
        return result
    raise ModelCallError(f"{what} failed after {retries + 1} attempts")
