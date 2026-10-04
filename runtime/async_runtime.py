"""
后台 asyncio 运行时
在独立线程中运行 asyncio 事件循环，网络任务全部提交到这里。
GUI 主线程通过 EventBus 与网络层通信，不直接跨线程调用。
"""
import asyncio
import threading
from concurrent.futures import Future
from typing import Coroutine, Any


class AsyncRuntime:
    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name="AsyncRuntime")
        self.thread.start()
        self._ready.wait(timeout=5)

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    def submit(self, coro: Coroutine) -> Future:
        """提交协程到后台循环，返回 Future"""
        if not self.loop:
            raise RuntimeError("AsyncRuntime 未启动")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread:
            self.thread.join(timeout=3)