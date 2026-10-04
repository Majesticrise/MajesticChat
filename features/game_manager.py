"""
游戏房间协调
- /host <游戏名> [端口]  主机广播房间信息
- /join <游戏名>         加入房间（显示 IP 和端口）
- /rooms                 列出所有房间
- /leave <游戏名>        退出房间
- /stopgame <游戏名>     主机结束房间
"""
import time
from typing import Any, Mapping

from core.event_bus import EventBus, Events
from core.protocol import MsgType
from network.control_channel import ControlChannel

ROOM_TIMEOUT = 300


class GameManager:
    def __init__(self, username: str, self_ip: str,
                 control: ControlChannel, event_bus: EventBus):
        self.username = username
        self.self_ip = self_ip
        self.control = control
        self.event_bus = event_bus
        self.rooms: dict[str, dict] = {}

    async def start(self):
        self.event_bus.subscribe("control_message", self._on_control_message)
        self.event_bus.subscribe("user_command", self._on_user_command)

    # ========== 命令处理 ==========
    def _on_user_command(self, data: Mapping[str, Any]):
        cmd = data.get("command", "").strip()
        if not cmd.startswith("/"):
            return
        parts = cmd.split()
        if not parts:
            return
        head = parts[0].lower()

        if head == "/host":
            self._cmd_host(parts)
        elif head == "/join":
            self._cmd_join(parts)
        elif head == "/rooms":
            self._cmd_rooms(parts)
        elif head == "/leave":
            self._cmd_leave(parts)
        elif head == "/stopgame":
            self._cmd_stop(parts)

    def _cmd_host(self, parts):
        if len(parts) < 2:
            self._sys_msg("用法: /host <游戏名> [端口]")
            return
        game_name = parts[1]
        port = 25565
        if len(parts) >= 3:
            try:
                port = int(parts[2])
            except ValueError:
                self._sys_msg("端口必须为数字")
                return
        if game_name in self.rooms:
            self._sys_msg(f"房间 '{game_name}' 已存在")
            return

        self.rooms[game_name] = {
            "host": self.username,
            "ip": self.self_ip,
            "port": port,
            "participants": {self.username},
            "ts": time.time(),
        }
        self._broadcast_rooms()
        self._sys_msg(f"已创建房间 {game_name} → {self.self_ip}:{port}")

    def _cmd_join(self, parts):
        if len(parts) < 2:
            self._sys_msg("用法: /join <游戏名>")
            return
        game_name = parts[1]
        room = self.rooms.get(game_name)
        if not room:
            self._sys_msg(f"未找到房间 '{game_name}'")
            return
        room["participants"].add(self.username)
        room["ts"] = time.time()
        self._sys_msg(
            f"已加入 {game_name}\n"
            f"  主机: {room['host']}\n"
            f"  地址: {room['ip']}:{room['port']}\n"
            f"  → 在游戏中手动输入该地址连接"
        )
        self._broadcast_rooms()

    def _cmd_rooms(self, parts):
        self._purge_expired()
        if not self.rooms:
            self._sys_msg("当前没有活动房间")
            return
        lines = ["当前活动房间:"]
        for name, info in self.rooms.items():
            lines.append(
                f"  {name}  主机:{info['host']}  "
                f"地址:{info['ip']}:{info['port']}  "
                f"参与者:{len(info['participants'])}"
            )
        self._sys_msg("\n".join(lines))

    def _cmd_leave(self, parts):
        if len(parts) < 2:
            self._sys_msg("用法: /leave <游戏名>")
            return
        game_name = parts[1]
        room = self.rooms.get(game_name)
        if not room:
            self._sys_msg(f"未找到房间 '{game_name}'")
            return
        room["participants"].discard(self.username)
        room["ts"] = time.time()
        self._sys_msg(f"已退出房间 {game_name}")
        self._broadcast_rooms()

    def _cmd_stop(self, parts):
        if len(parts) < 2:
            self._sys_msg("用法: /stopgame <游戏名>")
            return
        game_name = parts[1]
        room = self.rooms.get(game_name)
        if not room:
            self._sys_msg(f"未找到房间 '{game_name}'")
            return
        if room["host"] != self.username:
            self._sys_msg("只有房主可以结束房间")
            return
        del self.rooms[game_name]
        self._sys_msg(f"已结束房间 {game_name}")
        self._broadcast_rooms()

    # ========== 广播 ==========
    def _broadcast_rooms(self):
        self._update_gui()
        payload = {
            "type": MsgType.GAME_LIST,
            "rooms": {
                name: {
                    "host": info["host"],
                    "ip": info["ip"],
                    "port": info["port"],
                    "participants": list(info["participants"]),
                }
                for name, info in self.rooms.items()
            }
        }
        # 通过事件总线交给 main 的 runtime 广播
        self.event_bus.publish("game_broadcast_rooms", payload)

    def _update_gui(self):
        rooms_list = [
            {
                "name": name,
                "host": info["host"],
                "ip": info["ip"],
                "port": info["port"],
            }
            for name, info in self.rooms.items()
        ]
        self.event_bus.publish(Events.GAME_ROOM_LIST, rooms_list)

    # ========== 网络收到 ==========
    def _on_control_message(self, data: Mapping[str, Any]):
        msg = data.get("msg", {})
        msg_type = msg.get("type", "")
        if msg_type == MsgType.GAME_LIST:
            self._merge_rooms(msg.get("rooms", {}))
        elif msg_type == MsgType.GAME_STOP:
            game_name = msg.get("game_name")
            if game_name in self.rooms:
                del self.rooms[game_name]
                self._update_gui()
                self._sys_msg(f"房间 {game_name} 已被房主结束")

    def _merge_rooms(self, rooms_data: dict):
        changed = False
        for name, info in rooms_data.items():
            if name not in self.rooms:
                self.rooms[name] = {
                    "host": info.get("host", "?"),
                    "ip": info.get("ip", ""),
                    "port": int(info.get("port", 0)),
                    "participants": set(info.get("participants", [])),
                    "ts": time.time(),
                }
                changed = True
            else:
                self.rooms[name]["participants"] = set(info.get("participants", []))
                self.rooms[name]["ts"] = time.time()
                changed = True
        if changed:
            self._update_gui()

    def _purge_expired(self):
        now = time.time()
        expired = [n for n, info in self.rooms.items()
                   if now - info["ts"] > ROOM_TIMEOUT]
        for n in expired:
            del self.rooms[n]

    def _sys_msg(self, content: str):
        self.event_bus.publish(Events.SYSTEM_MESSAGE, {"content": content})