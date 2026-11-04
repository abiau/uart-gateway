import asyncio
import json
from pathlib import Path
import re
import unittest

from uart_gateway.protocol.codec import binary_decode, binary_encode, decode_frame, encode_frame, read_frame
from uart_gateway.protocol.errors import ProtocolError
from uart_gateway.protocol.messages import request, validate_params


class CodecTests(unittest.TestCase):
    def test_binary_round_trip_all_bytes(self):
        payload = bytes(range(256)) * 16
        self.assertEqual(binary_decode(binary_encode(payload)), payload)

    def test_reject_invalid_json_forms(self):
        packets = [b'{}\r\n', b'\n', b'[]\n', b'\xef\xbb\xbf{}\n', b'{"a":1,"a":2}\n',
                   b'{"x":{"a":1,"a":2}}\n', b'{"x":NaN}\n', b'{"x":1e999}\n',
                   b'{"x":"\\ud800"}\n', b'{"x":"\xff"}\n', b'{}', b'{}\n{}\n']
        for raw in packets:
            with self.subTest(raw=raw), self.assertRaises(ProtocolError):
                decode_frame(raw)

    def test_depth_and_frame_boundaries(self):
        with self.assertRaises(ProtocolError):
            decode_frame(b'{"x":' + b'[' * 16 + b'0' + b']' * 16 + b'}\n')
        valid = encode_frame({"x": "[\\\"{"})
        self.assertEqual(decode_frame(valid)["x"], "[\\\"{")
        message = {"x": "x" * (65536 - len(encode_frame({"x": ""})))}
        self.assertEqual(len(encode_frame(message)), 65536)
        message["x"] += "x"
        with self.assertRaises(ProtocolError) as error:
            encode_frame(message)
        self.assertEqual(error.exception.code, "FRAME_TOO_LARGE")

    def test_base64_rejects_empty_noncanonical_and_oversized(self):
        for value in ("", "Y Q==", "Zh==", "YQ", "!!!!", 42):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                binary_decode(value)
        with self.assertRaises(ProtocolError) as error:
            binary_decode(binary_encode(b"a" * 4097))
        self.assertEqual(error.exception.code, "PAYLOAD_TOO_LARGE")

    def test_ids_and_unknown_parameters(self):
        for value in ("0", "01", 1, "18446744073709551616"):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                request({"type": "request", "id": value, "method": "ping", "params": {}}, 0)
        with self.assertRaises(ProtocolError):
            request({"type": "request", "id": "2", "method": "ping", "params": {}}, 2)
        with self.assertRaises(ProtocolError):
            validate_params("ping", {"typo": True})
        with self.assertRaises(ProtocolError):
            validate_params("rx.subscribe", {"channel_id": "ch", "mode": "resume"})
        with self.assertRaises(ProtocolError):
            validate_params("hello", {"client_name": "x", "client_version": "1", "versions": [{"major": True, "minor": 0}]})

    def test_documented_json_contract_examples(self):
        # 測試資料以版本庫根目錄定位，不依賴執行時 cwd。
        spec = Path(__file__).parents[2] / "docs/protocol/v1.md"
        count = 0
        for block in re.findall(r'^```json\n(.*?)^```', spec.read_text(encoding="utf-8"), re.M | re.S):
            value = json.loads(block)
            self.assertEqual(decode_frame(encode_frame(value)), value)
            if value["type"] == "request":
                parsed = request(value, 0)
                validate_params(parsed.method, parsed.params)
            count += 1
        self.assertEqual(count, 22)


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_and_coalesced_frames(self):
        reader = asyncio.StreamReader(limit=65535)
        pending = asyncio.create_task(read_frame(reader))
        reader.feed_data(b'{"a":')
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        reader.feed_data(b'1}\n{"b":2}\n')
        self.assertEqual(await pending, {"a": 1})
        self.assertEqual(await read_frame(reader), {"b": 2})

    async def test_unterminated_overlimit_and_partial_eof(self):
        reader = asyncio.StreamReader(limit=65535)
        reader.feed_data(b'x' * 65536)
        with self.assertRaises(ProtocolError) as error:
            await read_frame(reader)
        self.assertEqual(error.exception.code, "FRAME_TOO_LARGE")
        reader = asyncio.StreamReader()
        reader.feed_data(b'{')
        reader.feed_eof()
        with self.assertRaises(ProtocolError):
            await read_frame(reader)
