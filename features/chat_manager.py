"""
文字聊天管理
- 监听用户输入 → 广播
- 监听网络消息 → 存库 + 通知 GUI
- 历史同步
"""
import asyncio
import time
from typing import Any, Mapping

from core.config import SYNC_BATCH_SIZE
from core.event_bus import EventBus, Events
from core.protocol import MsgType, make_chat
from network.control_channel import ControlChannel
from storage.database import Database


class ChatManager:
    def __init__(self, username: str, control: ControlChannel,
                 db: Database, event_bus: EventBus, runtime):
        self.username = username
        self.control = control
        self.db = db
        self.event_bus = event_bus
        self.runtime = runtime
        self._last_synced_msg_id = db.max_msg_id()
        self.msg_counter = 0

    async def start(self):
        self.event_bus.subscribe("user_send_message", self._on_user_send)
        self.event_bus.subscribe("control_message", self._on_control_message)

    # ========== 用户发出（从 GUI 线程调用）==========
    def _on_user_send(self, data: Mapping[str, Any]):
        content = data.get("content", "").strip()
        if not content:
            return
        # 用 runtime.submit 提交到后台 asyncio 循环
        self.runtime.submit(self._do_send(content))

    async def _do_send(self, content: str):
        t = time.time()
        self.msg_counter += 1
        msg_id = f"{self.username}_{int(t * 1000)}_{self.msg_counter}"
        payload = make_chat(self.username, content, t, msg_id)
        # 先存库
        self.db.save_message(t, self.username, content, msg_id, "chat")
        # 广播
        await self.control.broadcast(payload)
        # 通知 GUI
        self.event_bus.publish(
            Events.CHAT_SENT,
            {"content": content, "time": t}
        )

    # ========== 网络收到（从 asyncio 线程调用）==========
    def _on_control_message(self, data: Mapping[str, Any]):
        ip = data.get("ip")
        msg = data.get("msg", {})
        msg_type = msg.get("type", "")

        if msg_type == MsgType.CHAT:
            sender = msg.get("sender", "?")
            content = msg.get("content", "")
            t = msg.get("time", time.time())
            msg_id = msg.get("msg_id", "")
            self.db.save_message(t, sender, content, msg_id, "chat")
            self.event_bus.publish(
                Events.CHAT_RECEIVED,
                {"sender": sender, "content": content, "time": t}
            )

        elif msg_type == MsgType.PRIVATE:
            sender = msg.get("sender", "?")
            content = msg.get("content", "")
            t = msg.get("time", time.time())
            msg_id = msg.get("msg_id", "")
            self.db.save_message(t, sender, content, msg_id, "private")
            self.event_bus.publish(
                Events.CHAT_RECEIVED,
                {"sender": f"[私聊] {sender}", "content": content, "time": t}
            )

        elif msg_type == MsgType.SYNC_REQ:
            last_id = int(msg.get("last_msg_id", 0))
            self.runtime.submit(self._serve_sync(ip, last_id))

        elif msg_type == MsgType.SYNC_MSG:
            t = msg.get("time", time.time())
            sender = msg.get("sender", "?")
            content = msg.get("content", "")
            msg_id = msg.get("msg_id", "")
            self.db.save_message(t, sender, content, msg_id, "chat")

        elif msg_type == MsgType.SYNC_END:
            print(f"[Chat] 与 {ip} 同步完成")

    async def _serve_sync(self, ip: str, last_id: int):
        """响应对方的同步请求，分批发送缺失消息"""
        offset = 0
        while True:
            rows = self.db.get_since_id(last_id, SYNC_BATCH_SIZE, offset)
            if not rows:
                break
            for row in rows:
                _, t, sender, content, msg_id = row
                await self.control.send_to(ip, {
                    "type": MsgType.SYNC_MSG,
                    "time": t,
                    "sender": sender,
                    "content": content,
                    "msg_id": msg_id,
                })
            offset += SYNC_BATCH_SIZE
            await asyncio.sleep(0.02)
        await self.control.send_to(ip, {"type": MsgType.SYNC_END})

    async def request_sync(self, ip: str):
        """主动请求与某节点同步"""
        await self.control.send_to(ip, {
            "type": MsgType.SYNC_REQ,
            "last_msg_id": self._last_synced_msg_id,
        })