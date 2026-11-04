import unittest

from uart_gateway.server.session import Session


class Writer:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class SessionTests(unittest.TestCase):
    def test_slow_subscription_is_bounded_and_aborts_only_itself(self):
        writer, other = Writer(), Writer()
        session = Session("slow", writer, subscription_bytes=256)
        second = Session("other", other)
        message = {"type": "event", "event": "rx", "channel_id": "a",
                   "data": {"subscription_id": "sub", "payload": "x" * 100}}
        for _ in range(5):
            session.send(message)
        self.assertTrue(writer.closed)
        self.assertTrue(session.closed)
        self.assertLessEqual(session.used, session.maximum)
        self.assertFalse(second.closed)
        self.assertFalse(other.closed)

    def test_unsubscribe_removes_unsent_rx_and_returns_budget(self):
        session = Session("s", Writer())
        session.send({"event": "rx", "data": {"subscription_id": "sub"}})
        session.drop_subscription("sub")
        self.assertEqual(session.used, 0)
        self.assertFalse(session.order)
        self.assertFalse(session.data)

    def test_control_queue_has_total_limit(self):
        writer = Writer()
        session = Session("s", writer, queue_bytes=100)
        for _ in range(10):
            session.send({"type": "response", "id": "1", "ok": True, "result": {}})
        self.assertTrue(writer.closed)
        self.assertLessEqual(session.used, 100)
