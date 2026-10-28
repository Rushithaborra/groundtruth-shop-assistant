"""call_model: every model call has a timeout, retries temporary errors with back-off, and gives up cleanly."""

import threading
import time
import unittest
from concurrent import futures
from unittest import mock

from groundtruth.models import calls
from groundtruth.models.calls import ModelCallError, call_model


class CallModelTests(unittest.TestCase):
    def tearDown(self):
        # a test may leave a deliberately slow call finishing in the worker; don't let it leak into the next test
        futures.wait(calls._running, timeout=10)

    def test_a_working_call_returns_its_result(self):
        self.assertEqual(call_model(lambda: 42, what="test", timeout_s=1, retries=0, backoff_s=0), 42)

    def test_a_temporary_error_is_retried_with_growing_back_off(self):
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("busy")
            return "ok"

        with mock.patch.object(calls.time, "sleep") as sleep:
            self.assertEqual(call_model(flaky, what="test", timeout_s=1, retries=2, backoff_s=0.5), "ok")
        self.assertEqual(len(attempts), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.5, 1.0])

    def test_giving_up_after_the_last_retry_raises_model_call_error(self):
        def broken():
            raise RuntimeError("native library error")

        with mock.patch.object(calls.time, "sleep"), self.assertRaises(ModelCallError):
            call_model(broken, what="test", timeout_s=1, retries=1, backoff_s=0.1)

    def test_bad_input_fails_at_once_without_retrying(self):
        attempts = []

        def too_long():
            attempts.append(1)
            raise ValueError("Requested tokens (3815) exceed context window of 2048")

        with mock.patch.object(calls.time, "sleep") as sleep, self.assertRaisesRegex(ModelCallError, "rejected"):
            call_model(too_long, what="test", timeout_s=1, retries=3, backoff_s=0.5)
        self.assertEqual((len(attempts), sleep.call_count), (1, 0))

    def test_a_slow_call_times_out_without_waiting_for_it(self):
        release = threading.Event()
        try:
            with self.assertRaisesRegex(ModelCallError, "timed out"):
                call_model(lambda: release.wait(5), what="test", timeout_s=0.05, retries=3, backoff_s=0)
        finally:
            release.set()  # let the abandoned call finish so the worker is free for other tests

    def test_a_call_after_a_timeout_fails_fast_instead_of_queueing(self):
        release = threading.Event()
        try:
            with self.assertRaises(ModelCallError):
                call_model(lambda: release.wait(5), what="slow", timeout_s=0.05, retries=0, backoff_s=0)
            started = time.perf_counter()
            with self.assertRaisesRegex(ModelCallError, "busy"):
                call_model(lambda: 1, what="next", timeout_s=5, retries=0, backoff_s=0)
            self.assertLess(time.perf_counter() - started, 0.5)  # didn't wait for the slow call or its timeout
        finally:
            release.set()
        calls._running[0].result(timeout=5)  # the abandoned call finishes; the model is usable again
        self.assertEqual(call_model(lambda: 2, what="after", timeout_s=1, retries=0, backoff_s=0), 2)

    def test_programming_errors_are_not_swallowed(self):
        def bug():
            raise KeyError("typo")

        with self.assertRaises(KeyError):
            call_model(bug, what="test", timeout_s=1, retries=2, backoff_s=0)


if __name__ == "__main__":
    unittest.main()
