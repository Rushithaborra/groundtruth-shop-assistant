"""call_model: every model call has a timeout, retries temporary errors with back-off, and gives up cleanly."""

import threading
import unittest
from unittest import mock

from groundtruth.models import calls
from groundtruth.models.calls import ModelCallError, call_model


class CallModelTests(unittest.TestCase):
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
            raise ValueError("context too long")

        with mock.patch.object(calls.time, "sleep"), self.assertRaises(ModelCallError):
            call_model(broken, what="test", timeout_s=1, retries=1, backoff_s=0.1)

    def test_a_slow_call_times_out_without_waiting_for_it(self):
        release = threading.Event()
        try:
            with self.assertRaisesRegex(ModelCallError, "timed out"):
                call_model(lambda: release.wait(5), what="test", timeout_s=0.05, retries=3, backoff_s=0)
        finally:
            release.set()  # let the abandoned call finish so the worker is free for other tests

    def test_programming_errors_are_not_swallowed(self):
        def bug():
            raise KeyError("typo")

        with self.assertRaises(KeyError):
            call_model(bug, what="test", timeout_s=1, retries=2, backoff_s=0)


if __name__ == "__main__":
    unittest.main()
