"""
事件总线：解耦各模块，网络/音频层只发事件，GUI 层订阅事件更新。
线程安全：使用 threading.Lock 保护订阅表。
"""
import threading
from collections import defaultdict
from typing import Callable, Any


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, event: str, callback: Callable[[Any], None]):
        with self._lock:
            self._subscribers[event].append(callback)

    def unsubscribe(self, event: str, callback: Callable[[Any], None]):
        with self._lock:
            if callback in self._subscribers[event]:
                self._subscribers[event].remove(callback)

    def publish(self, event: str, data: Any = None):
        with self._lock:
            callbacks = list(self._subscribers.get(event, []))
        for cb in callbacks:
            try:
                cb(data)
            except Exception as e:
                print(f"[EventBus] 事件 {event} 回调异常: {e}")


# ========== 事件常量 ==========
class Events:
    # 网络层
    PEER_DISCOVERED = "peer_discovered"      # 底层发现节点（触发连接），data: {"ip": str}
    PEER_JOINED = "peer_joined"              # 握手完成，data: {"ip": str, "name": str}
    PEER_LEFT = "peer_left"                  # data: {"ip": str, "name": str}
    PEER_LIST_UPDATED = "peer_list_updated"
    CONNECTION_TYPE = "connection_type"

    # 聊天
    CHAT_RECEIVED = "chat_received"
    CHAT_SENT = "chat_sent"
    PRIVATE_RECEIVED = "private_received"
    SYSTEM_MESSAGE = "system_message"

    # 文件
    FILE_STARTED = "file_started"
    FILE_PROGRESS = "file_progress"
    FILE_FINISHED = "file_finished"
    FILE_FAILED = "file_failed"

    # 游戏房间
    GAME_ROOM_ADDED = "game_room_added"
    GAME_ROOM_REMOVED = "game_room_removed"
    GAME_ROOM_LIST = "game_room_list"

    # 语音
    VOICE_STATE = "voice_state"
    VOICE_PEER_SPEAKING = "voice_peer_speaking"
    VOICE_PEER_JOINED = "voice_peer_joined"
    VOICE_PEER_LEFT = "voice_peer_left"

    # 应用
    APP_QUIT = "app_quit"