"""
后台 asyncio 运行时
在独立线程中运行 asyncio 事件循环，网络任务全部提交到这里。
GUI 主线程通过 EventBus 与网络层通信，不直接跨线程调用。
"""
import asyncio
import threading
from concurrent.futures import Future
from typing import Coroutine


class AsyncRuntime:
    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._shutdown = threading.Event()

    def start(self):
        if self.loop is not None and self.loop.is_running():
            return

        self._shutdown.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="AsyncRuntime")
        self.thread.start()
        self._ready.wait(timeout=5)

    def _run(self):
        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                pending = [
                    task for task in asyncio.all_tasks(loop)
                    if not task.done()
                ]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            finally:
                try:
                    loop.close()
                finally:
                    asyncio.set_event_loop(None)

    def submit(self, coro: Coroutine) -> Future:
        """提交协程到后台循环，返回 Future"""
        if not self.loop or not self.loop.is_running():
            raise RuntimeError("AsyncRuntime 未启动")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self):
        self._shutdown.set()
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self._stop_loop)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        self.thread = None

    def _stop_loop(self):
        loop = self.loop
        if loop is None:
            return
        try:
            pending = [
                task for task in asyncio.all_tasks(loop)
                if not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                loop.create_task(self._cancel_and_stop(loop, pending))
            else:
                loop.stop()
        except Exception:
            try:
                loop.stop()
            except Exception:
                pass

    async def _cancel_and_stop(self, loop: asyncio.AbstractEventLoop, pending):
        await asyncio.gather(*pending, return_exceptions=True)
        if loop.is_running():
            loop.stop()