"""可前進的單調時鐘與有期限的非同步等待。"""

import asyncio
import time


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


async def eventually(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("未在期限內達到預期狀態")
        await asyncio.sleep(0.002)
