"""
全局热键管理器：监听 PTT 按键，发布 user_ptt_press / user_ptt_release 事件
支持组合键（如 ctrl+shift+v），原理：只监听触发键，修饰键用 is_pressed 检查
"""
import threading
from typing import Any, List, Optional

from core.event_bus import EventBus


class HotkeyManager:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._keyboard: Any | None = None
        self._current_key: Optional[str] = None
        self._modifiers: List[str] = []
        self._trigger_key: Optional[str] = None
        self._ptt_active: bool = False
        self._lock = threading.Lock()

    def start(self) -> bool:
        if self._keyboard is not None:
            return True
        try:
            import keyboard  # type: ignore[import-not-found]
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

            # 解析组合键
            parts = [p.strip().lower() for p in key.split("+") if p.strip()]
            if not parts:
                print(f"[Hotkey] 无效的组合键: {key}")
                return False

            modifiers = parts[:-1]
            trigger = parts[-1]

            try:
                self._modifiers = modifiers
                self._trigger_key = trigger
                self._ptt_active = False

                # 只监听触发键的按下与松开
                self._keyboard.on_press_key(trigger, self._on_trigger_press)
                self._keyboard.on_release_key(trigger, self._on_trigger_release)

                self._current_key = key
                print(f"[Hotkey] PTT 已注册: {key}")
                return True
            except Exception as e:
                print(f"[Hotkey] 注册 {key} 失败: {e}")
                self._modifiers = []
                self._trigger_key = None
                self._current_key = None
                return False

    # ========== 触发键回调 ==========
    def _on_trigger_press(self, event=None):
        with self._lock:
            if self._ptt_active:
                return  # 已经激活，避免 Windows 自动重复触发
            if self._check_modifiers_pressed():
                self._ptt_active = True
                self.event_bus.publish("user_ptt_press")

    def _on_trigger_release(self, event=None):
        with self._lock:
            if not self._ptt_active:
                return
            self._ptt_active = False
            self.event_bus.publish("user_ptt_release")

    def _check_modifiers_pressed(self) -> bool:
        if not self._modifiers or not self._keyboard:
            return True
        try:
            return all(self._keyboard.is_pressed(m) for m in self._modifiers)
        except Exception:
            return False

    # ========== 注销 ==========
    def unregister_ptt(self):
        if not self._keyboard or not self._trigger_key:
            self._reset_state()
            return
        try:
            self._keyboard.unhook_key(self._trigger_key)
        except Exception as e:
            print(f"[Hotkey] 注销 {self._trigger_key} 失败: {e}")
        self._reset_state()

    def _reset_state(self):
        self._modifiers = []
        self._trigger_key = None
        self._ptt_active = False
        self._current_key = None

    def stop(self):
        self.unregister_ptt()