"""SSE fan-out: every connected client gets every event, and unsubscribing cleans up.

No HTTP here on purpose — the SSE handler loops forever, so a real request would
wedge the single-threaded test server in test_server.py.
"""
import queue
import unittest

from token_dashboard.server import _SUBS, _publish, _subscribe


class SseFanoutTests(unittest.TestCase):
    def test_every_subscriber_gets_the_event(self):
        with _subscribe() as a, _subscribe() as b:
            _publish({"type": "scan", "n": 1})
            self.assertEqual(a.get(timeout=1), {"type": "scan", "n": 1})
            self.assertEqual(b.get(timeout=1), {"type": "scan", "n": 1})

    def test_unsubscribe_on_exit(self):
        before = len(_SUBS)
        with _subscribe():
            self.assertEqual(len(_SUBS), before + 1)
        self.assertEqual(len(_SUBS), before)

    def test_unsubscribe_on_exception(self):
        before = len(_SUBS)
        with self.assertRaises(RuntimeError):
            with _subscribe():
                raise RuntimeError("client hung up")
        self.assertEqual(len(_SUBS), before)

    def test_publish_with_no_subscribers_is_a_noop(self):
        _publish({"type": "scan"})  # must not raise

    def test_events_do_not_leak_across_subscriptions(self):
        with _subscribe() as a:
            _publish({"type": "scan", "n": 1})
            a.get(timeout=1)
        with _subscribe() as b:
            with self.assertRaises(queue.Empty):
                b.get(timeout=0.05)


if __name__ == "__main__":
    unittest.main()
