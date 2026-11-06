import unittest
import asyncio
import json

from uart_gateway.core.models import DomainError, Event
from uart_gateway.server.session import Session, replay_cost
from tests.fakes import eventually


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


class AsyncWriter(Writer):
    def __init__(self):
        super().__init__()
        self.messages = []
        self.gate = None

    def write(self, raw):
        self.messages.append(json.loads(raw))

    async def drain(self):
        if self.gate is not None:
            await self.gate.wait()
        await asyncio.sleep(0)

    async def wait_closed(self):
        pass


def historical(sub, index=0):
    return Event("rx", "a", 1, "epoch", {"subscription_id": sub, "payload": b"history", "index": index})


def encode_history(event):
    return {"event": "rx", "data": {"subscription_id": event.data["subscription_id"],
            "delivery": "replay", "index": event.data["index"]}}


class ReplaySessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.writer = AsyncWriter()
        self.session = Session("s", self.writer, subscription_bytes=128)

    async def asyncTearDown(self):
        if self.writer.gate is not None:
            self.writer.gate.set()
        await self.session.close()

    def run_writer(self):
        self.session.task = asyncio.create_task(self.session.run())

    async def test_long_snapshot_is_incremental_response_first_and_live_last(self):
        events = [historical("sub", i) for i in range(1000)]
        self.session.replay("sub", events, encode_history)
        self.session.send({"type": "response", "id": "1"})
        self.session.send({"event": "rx", "data": {"subscription_id": "sub", "delivery": "live"}})
        self.assertLessEqual(self.session.used, self.session.maximum)
        self.assertFalse(self.session.closed)
        self.run_writer()
        await eventually(lambda: len(self.writer.messages) == 1002)
        self.assertEqual(self.writer.messages[0]["type"], "response")
        self.assertEqual([m["data"]["index"] for m in self.writer.messages[1:-1]], list(range(1000)))
        self.assertEqual(self.writer.messages[-1]["data"]["delivery"], "live")
        self.assertEqual((self.session.used, self.session.replay_used), (0, 0))

    async def test_multiple_subscriptions_are_round_robin_and_drop_releases_snapshots(self):
        for sub in ("a", "b"):
            self.session.replay(sub, [historical(sub, i) for i in range(3)], encode_history)
        self.session.drop_subscription("a")
        self.assertEqual(self.session.replay_used, 3 * replay_cost(historical("b")))
        self.session.replay("a", [historical("a", i) for i in range(3)], encode_history)
        self.run_writer()
        await eventually(lambda: len(self.writer.messages) == 6)
        self.assertEqual([m["data"]["subscription_id"] for m in self.writer.messages], ["b", "a"] * 3)

    async def test_replay_budget_rejection_does_not_close_existing_subscription(self):
        event = historical("a")
        self.session.replay_max = replay_cost(event)
        self.session.replay("a", [event], encode_history)
        with self.assertRaises(DomainError) as error:
            self.session.replay("b", [historical("b")], encode_history)
        self.assertEqual(error.exception.code, "QUEUE_FULL")
        self.assertFalse(self.session.closed)
        self.assertEqual(list(self.session.replays), ["a"])

    async def test_live_queue_remains_bounded_during_stalled_replay_and_close_frees_everything(self):
        self.writer.gate = asyncio.Event()
        self.session.replay("a", [historical("a", i) for i in range(1000)], encode_history)
        self.run_writer()
        await eventually(lambda: bool(self.writer.messages))
        with self.assertLogs("uart_gateway.server.session", level="WARNING"):
            for _ in range(10):
                self.session.send({"event": "rx", "data": {"subscription_id": "a", "payload": "x" * 64}})
        self.assertTrue(self.session.closed)
        self.assertEqual((self.session.used, self.session.replay_used), (0, 0))
        self.assertFalse(self.session.order)
        self.assertFalse(self.session.replays)

    async def test_replay_deadline_frees_snapshot_and_logs_reason(self):
        self.session.replay_timeout = 0
        self.session.replay("a", [historical("a")], encode_history)
        with self.assertLogs("uart_gateway.server.session", level="WARNING") as logs:
            self.run_writer()
            await eventually(lambda: self.session.closed)
        self.assertIn("歷史重播等待超時", "\n".join(logs.output))
        self.assertEqual(self.session.replay_used, 0)

    async def test_unsubscribe_during_write_discards_remaining_history_and_allows_control_response(self):
        self.writer.gate = asyncio.Event()
        self.session.replay("a", [historical("a", i) for i in range(1000)], encode_history)
        self.run_writer()
        await eventually(lambda: bool(self.writer.messages))
        pending = self.session.replays["a"]
        self.session.drop_subscription("a")
        self.assertFalse(pending.events)
        self.session.send({"type": "response", "id": "unsubscribe"})
        self.writer.gate.set()
        await eventually(lambda: len(self.writer.messages) == 2)
        self.assertEqual(self.writer.messages[-1]["id"], "unsubscribe")
        self.assertEqual((self.session.used, self.session.replay_used), (0, 0))

    async def test_gap_precedes_replay_and_live_for_the_same_subscription(self):
        gap = Event("gap", "a", 1, "epoch", {"subscription_id": "a"})
        self.session.replay("a", [gap, historical("a")], lambda event: {"event": event.kind})
        self.session.send({"event": "rx", "data": {"subscription_id": "a", "delivery": "live"}})
        self.run_writer()
        await eventually(lambda: len(self.writer.messages) == 3)
        self.assertEqual([m["event"] for m in self.writer.messages], ["gap", "rx", "rx"])
        self.assertEqual(self.writer.messages[-1]["data"]["delivery"], "live")
