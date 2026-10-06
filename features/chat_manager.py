"""
文字聊天管理 + 命令分发
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
        self._seen_message_ids: set[str] = set()
        self._max_seen_messages = 4096

    async def start(self):
        self.event_bus.subscribe("user_send_message", self._on_user_send)
        self.event_bus.subscribe("control_message", self._on_control_message)

    # ========== 用户输入 ==========
    def _on_user_send(self, data: Mapping[str, Any]):
        content = data.get("content", "").strip()
        if not content:
            return
        if content.startswith("/"):
            self._handle_command(content)
            return
        self.runtime.submit(self._do_send(content))

    def _handle_command(self, content: str):
        parts = content.split()
        head = parts[0].lower()

        # 游戏相关：转发给 GameManager
        if head in ("/host", "/join", "/rooms", "/leave", "/stopgame"):
            self.event_bus.publish("user_command", {"command": content})
            return

        # /send <昵称> <路径>
        if head == "/send":
            if len(parts) < 3:
                self._sys_msg("用法: /send <昵称> <文件路径>")
                return
            target_name = parts[1]
            file_path = " ".join(parts[2:])
            self.event_bus.publish("user_send_file", {
                "target_name": target_name,
                "file_path": file_path,
            })
            return

        # /cancel <id> 或 /cancel all
        if head == "/cancel":
            if len(parts) < 2:
                self._sys_msg("用法: /cancel <传输ID> 或 /cancel all")
                return
            self.event_bus.publish("user_cancel_file", {"target": parts[1]})
            return

        # /help
        if head == "/help":
            self._sys_msg(
                "可用命令:\n"
                "  /send <昵称> <路径>   发送文件\n"
                "  /cancel <传输ID|all>  取消文件传输\n"
                "  /host <游戏> [端口]   创建游戏房间\n"
                "  /join <游戏>          加入游戏房间\n"
                "  /rooms                列出所有房间\n"
                "  /leave <游戏>         退出房间\n"
                "  /stopgame <游戏>      结束自己的房间"
            )
            return

        self._sys_msg(f"未知命令: {head}")

    async def _do_send(self, content: str):
        t = time.time()
        self.msg_counter += 1
        msg_id = f"{self.username}_{int(t * 1000)}_{self.msg_counter}"
        payload = make_chat(self.username, content, t, msg_id)
        self.db.save_message(t, self.username, content, msg_id, "chat")
        await self.control.broadcast(payload)
        self.event_bus.publish(Events.CHAT_SENT, {"content": content, "time": t})

    # ========== 网络收到 ==========
    def _on_control_message(self, data: Mapping[str, Any]):
        ip = data.get("ip")
        msg = data.get("msg", {})
        msg_type = msg.get("type", "")

        if msg_type == MsgType.CHAT:
            sender = msg.get("sender", "?")
            content = msg.get("content", "")
            t = msg.get("time", time.time())
            msg_id = msg.get("msg_id") or f"{sender}:{t}:{content}"
            if sender == self.username:
                return
            if msg_id in self._seen_message_ids:
                return
            self._seen_message_ids.add(msg_id)
            if len(self._seen_message_ids) > self._max_seen_messages:
                self._seen_message_ids = set(list(self._seen_message_ids)[-self._max_seen_messages:])
            if self.db.save_message(t, sender, content, msg_id, "chat"):
                self.event_bus.publish(
                    Events.CHAT_RECEIVED,
                    {"sender": sender, "content": content, "time": t, "msg_id": msg_id}
                )

        elif msg_type == MsgType.PRIVATE:
            sender = msg.get("sender", "?")
            content = msg.get("content", "")
            t = msg.get("time", time.time())
            msg_id = msg.get("msg_id") or f"{sender}:{t}:{content}"
            if sender == self.username:
                return
            if msg_id in self._seen_message_ids:
                return
            self._seen_message_ids.add(msg_id)
            if len(self._seen_message_ids) > self._max_seen_messages:
                self._seen_message_ids = set(list(self._seen_message_ids)[-self._max_seen_messages:])
            if self.db.save_message(t, sender, content, msg_id, "private"):
                self.event_bus.publish(
                    Events.CHAT_RECEIVED,
                    {"sender": f"[私聊] {sender}", "content": content, "time": t, "msg_id": msg_id}
                )

        elif msg_type == MsgType.SYNC_REQ:
            last_id = int(msg.get("last_msg_id", 0))
            peer_ip = ip if isinstance(ip, str) else ""
            self.runtime.submit(self._serve_sync(peer_ip, last_id))

        elif msg_type == MsgType.SYNC_MSG:
            t = msg.get("time", time.time())
            sender = msg.get("sender", "?")
            content = msg.get("content", "")
            msg_id = msg.get("msg_id") or f"{sender}:{t}:{content}"
            if sender == self.username:
                return
            if msg_id in self._seen_message_ids:
                return
            self._seen_message_ids.add(msg_id)
            if len(self._seen_message_ids) > self._max_seen_messages:
                self._seen_message_ids = set(list(self._seen_message_ids)[-self._max_seen_messages:])
            self.db.save_message(t, sender, content, msg_id, "chat")

        elif msg_type == MsgType.SYNC_END:
            print(f"[Chat] 与 {ip} 同步完成")

    async def _serve_sync(self, ip: str, last_id: int):
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

    def _sys_msg(self, content: str):
        self.event_bus.publish(Events.SYSTEM_MESSAGE, {"content": content})