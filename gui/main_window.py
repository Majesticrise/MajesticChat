"""
主窗口：四区域布局
┌─────────────────────────────────────────────┐
│ 用户列表   │        聊天记录                 │
│           │                                 │
├───────────┴─────────────────────────────────┤
│  游戏房间列表                                │
├─────────────────────────────────────────────┤
│  语音控制栏 [VAD/PTT] [音量] [静音]          │
├─────────────────────────────────────────────┤
│  [输入框]                          [发送]    │
└─────────────────────────────────────────────┘
"""
import tkinter as tk
from tkinter import ttk, scrolledtext
import queue
import time
from typing import Optional

from core.event_bus import EventBus, Events


class MainWindow:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.root = tk.Tk()
        self.root.title("MajesticLink - 私人 P2P 联机")
        self.root.geometry("900x650")
        self.root.minsize(700, 500)

        # UI 更新队列：网络/音频线程只往这里塞数据，主线程定时拉取
        self.ui_queue: queue.Queue = queue.Queue()

        self._build_layout()
        self._bind_events()

        # 启动队列轮询
        self.root.after(50, self._poll_ui_queue)

        # 关闭窗口时发布退出事件
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ========== 布局 ==========
    def _build_layout(self):
        # 主分割：上方 (用户列表 + 聊天)，下方 (游戏房间 + 语音 + 输入)
        main_paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # ---- 上方区域：横向分割 ----
        top_frame = ttk.Frame(main_paned)
        main_paned.add(top_frame, weight=3)

        top_paned = ttk.PanedWindow(top_frame, orient=tk.HORIZONTAL)
        top_paned.pack(fill=tk.BOTH, expand=True)

        # 用户列表
        user_frame = ttk.LabelFrame(top_paned, text="在线用户")
        top_paned.add(user_frame, weight=1)
        self.user_listbox = tk.Listbox(user_frame, activestyle="none")
        self.user_listbox.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)

        # 聊天记录
        chat_frame = ttk.LabelFrame(top_paned, text="聊天记录")
        top_paned.add(chat_frame, weight=3)
        self.chat_text = scrolledtext.ScrolledText(
            chat_frame, wrap=tk.WORD, state=tk.DISABLED, font=("Microsoft YaHei", 10)
        )
        self.chat_text.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)

        # ---- 下方区域 ----
        bottom_frame = ttk.Frame(main_paned)
        main_paned.add(bottom_frame, weight=1)

        # 游戏房间
        game_frame = ttk.LabelFrame(bottom_frame, text="游戏房间")
        game_frame.pack(fill=tk.X, padx=3, pady=3)
        self.game_tree = ttk.Treeview(
            game_frame,
            columns=("name", "host", "addr"),
            show="headings",
            height=3,
        )
        self.game_tree.heading("name", text="游戏")
        self.game_tree.heading("host", text="主机")
        self.game_tree.heading("addr", text="地址")
        self.game_tree.column("name", width=120)
        self.game_tree.column("host", width=100)
        self.game_tree.column("addr", width=200)
        self.game_tree.pack(fill=tk.X, padx=3, pady=3)

        # 语音控制栏
        voice_frame = ttk.Frame(bottom_frame)
        voice_frame.pack(fill=tk.X, padx=3, pady=3)

        self.voice_mode = tk.StringVar(value="vad")
        ttk.Radiobutton(
            voice_frame, text="自由发言 (VAD)",
            variable=self.voice_mode, value="vad"
        ).pack(side=tk.LEFT, padx=3)
        ttk.Radiobutton(
            voice_frame, text="按键说话 (PTT)",
            variable=self.voice_mode, value="ptt"
        ).pack(side=tk.LEFT, padx=3)

        ttk.Label(voice_frame, text="麦克风音量:").pack(side=tk.LEFT, padx=(20, 3))
        self.mic_volume = tk.Scale(
            voice_frame, from_=0, to=100, orient=tk.HORIZONTAL, length=120
        )
        self.mic_volume.set(80)
        self.mic_volume.pack(side=tk.LEFT)

        self.voice_btn = ttk.Button(
            voice_frame, text="加入语音", command=self._toggle_voice
        )
        self.voice_btn.pack(side=tk.RIGHT, padx=3)

        # 输入框
        input_frame = ttk.Frame(bottom_frame)
        input_frame.pack(fill=tk.X, padx=3, pady=(0, 5))
        self.input_entry = ttk.Entry(input_frame)
        self.input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.input_entry.bind("<Return>", lambda e: self._on_send())
        ttk.Button(input_frame, text="发送", command=self._on_send).pack(side=tk.RIGHT)

    # ========== 事件订阅 ==========
    def _bind_events(self):
        self.event_bus.subscribe(Events.PEER_JOINED, self._on_peer_joined)
        self.event_bus.subscribe(Events.PEER_LEFT, self._on_peer_left)
        self.event_bus.subscribe(Events.CHAT_RECEIVED, self._on_chat_received)
        self.event_bus.subscribe(Events.CHAT_SENT, self._on_chat_sent)
        self.event_bus.subscribe(Events.SYSTEM_MESSAGE, self._on_system_message)
        self.event_bus.subscribe(Events.GAME_ROOM_LIST, self._on_game_room_list)
        self.event_bus.subscribe(Events.VOICE_STATE, self._on_voice_state)

    # ========== 事件处理（在调用线程中执行，一律塞进队列）==========
    def _on_peer_joined(self, data):
        self.ui_queue.put(("peer_joined", data))

    def _on_peer_left(self, data):
        self.ui_queue.put(("peer_left", data))

    def _on_chat_received(self, data):
        self.ui_queue.put(("chat_received", data))

    def _on_chat_sent(self, data):
        self.ui_queue.put(("chat_sent", data))

    def _on_system_message(self, data):
        self.ui_queue.put(("system_message", data))

    def _on_game_room_list(self, data):
        self.ui_queue.put(("game_room_list", data))

    def _on_voice_state(self, data):
        self.ui_queue.put(("voice_state", data))

    # ========== 主线程轮询 UI 队列 ==========
    def _poll_ui_queue(self):
        try:
            while True:
                kind, data = self.ui_queue.get_nowait()
                self._apply_ui_update(kind, data)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_ui_queue)

    def _apply_ui_update(self, kind: str, data):
        if kind == "peer_joined":
            name = data.get("name", "?")
            items = self.user_listbox.get(0, tk.END)
            if name not in items:
                self.user_listbox.insert(tk.END, name)
        elif kind == "peer_left":
            name = data.get("name", "?")
            items = self.user_listbox.get(0, tk.END)
            if name in items:
                idx = items.index(name)
                self.user_listbox.delete(idx)
        elif kind == "chat_received":
            sender = data.get("sender", "?")
            content = data.get("content", "")
            t = data.get("time")
            time_str = time.strftime("%H:%M:%S", time.localtime(t)) if t else ""
            self._append_chat(f"[{time_str}] [{sender}] {content}")
        elif kind == "chat_sent":
            content = data.get("content", "")
            t = data.get("time")
            time_str = time.strftime("%H:%M:%S", time.localtime(t)) if t else ""
            self._append_chat(f"[{time_str}] [我] {content}")
        elif kind == "system_message":
            self._append_chat(f"[系统] {data.get('content', '')}")
        elif kind == "game_room_list":
            self._refresh_game_rooms(data)
        elif kind == "voice_state":
            in_voice = data.get("in_voice", False)
            self.voice_btn.config(text="退出语音" if in_voice else "加入语音")

    def _append_chat(self, text: str):
        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.insert(tk.END, text + "\n")
        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

    def _refresh_game_rooms(self, rooms: list):
        for item in self.game_tree.get_children():
            self.game_tree.delete(item)
        for room in rooms:
            self.game_tree.insert(
                "", tk.END,
                values=(
                    room.get("name"),
                    room.get("host"),
                    f"{room.get('ip')}:{room.get('port')}"
                )
            )

    # ========== 用户操作 ==========
    def _on_send(self):
        text = self.input_entry.get().strip()
        if not text:
            return
        self.input_entry.delete(0, tk.END)
        self.event_bus.publish("user_send_message", {"content": text})

    def _toggle_voice(self):
        self.event_bus.publish("user_toggle_voice")

    def _on_close(self):
        self.event_bus.publish(Events.APP_QUIT)
        self.root.destroy()

    def run(self):
        self.root.mainloop()