"""
全局热键管理器：监听 PTT 按键，发布 user_ptt_press / user_ptt_release 事件
"""
import threading
from typing import Any, Optional

from core.event_bus import EventBus


class HotkeyManager:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._keyboard: Any | None = None
        self._current_key: Optional[str] = None
        self._press_callback = None
        self._release_callback = None
        self._lock = threading.Lock()

    def start(self) -> bool:
        if self._keyboard is not None:
            return True
        try:
            import keyboard
            self._keyboard = keyboard
            print("[Hotkey] 管理器已启动")
            return True
        except Exception as e:
            print(f"[Hotkey] 不可用: {e}")
            return False

    def register_ptt(self, key: str) -> bool:
        if not self._keyboard or not key:
            return False
        with self._lock:
            self.unregister_ptt()
            try:
                self._press_callback = lambda e=None: self.event_bus.publish("user_ptt_press")
                self._release_callback = lambda e=None: self.event_bus.publish("user_ptt_release")
                self._keyboard.on_press_key(key, self._press_callback)
                self._keyboard.on_release_key(key, self._release_callback)
                self._current_key = key
                print(f"[Hotkey] PTT 已注册: {key}")
                return True
            except Exception as e:
                print(f"[Hotkey] 注册 {key} 失败: {e}")
                # 注册失败时把回调清掉，避免残留
                self._press_callback = None
                self._release_callback = None
                return False

    def unregister_ptt(self):
        if not self._keyboard or not self._current_key:
            return
        try:
            # keyboard 库的正确 API：unhook_key 接受 key 字符串
            self._keyboard.unhook_key(self._current_key)
        except Exception as e:
            print(f"[Hotkey] 注销 {self._current_key} 失败: {e}")
        self._press_callback = None
        self._release_callback = None
        self._current_key = None

    def stop(self):
        self.unregister_ptt()