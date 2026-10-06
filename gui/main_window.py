"""
主窗口：四区域布局
- 用户列表用 Treeview，显示 IP、昵称、延迟
- 定时刷新延迟
- 系统托盘 + 消息通知 + @提及高亮
"""
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, simpledialog, messagebox
import queue
import time
from typing import Optional

try:
    import pystray
except Exception:
    pystray = None

try:
    from PIL import Image
except Exception:
    Image = None

from core.event_bus import EventBus, Events


class MainWindow:
    def __init__(self, event_bus: EventBus, control=None, config: Optional[dict] = None):
        self.event_bus = event_bus
        self.control = control
        self.config = config or {}
        self.config_callback = None
        self.current_username = str(self.config.get("username", "Anonymous")).strip() or "Anonymous"
        self.is_minimized = False
        self.tray_icon = None

        # ===== 先创建 Tk 根窗口 =====
        self.root = tk.Tk()
        self.root.title("MajesticLink - 私人 P2P 联机")
        self.root.geometry("960x680")
        self.root.minsize(760, 520)

        self.ui_queue: queue.Queue = queue.Queue()
        self._peer_items: dict[str, str] = {}

        self._current_ptt_key = str(self.config.get("ptt_key", "ctrl+shift+v")).strip() or "ctrl+shift+v"
        self.voice_mode = tk.StringVar()

        self._build_layout()
        self._bind_events()
        self._apply_saved_voice_config()

        self.root.after(50, self._poll_ui_queue)
        self.root.after(1000, self._refresh_latency)

        self.root.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)

        # ===== 所有 Tk 组件就绪后再初始化托盘 =====
        self._setup_tray()

    # ========== 布局 ==========
    def _build_layout(self):
        main_paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        top_frame = ttk.Frame(main_paned)
        main_paned.add(top_frame, weight=3)

        top_paned = ttk.PanedWindow(top_frame, orient=tk.HORIZONTAL)
        top_paned.pack(fill=tk.BOTH, expand=True)

        # 用户列表
        user_frame = ttk.LabelFrame(top_paned, text="在线用户")
        top_paned.add(user_frame, weight=1)
        self.user_tree = ttk.Treeview(
            user_frame,
            columns=("name", "latency"),
            show="tree headings",
            height=10,
        )
        self.user_tree.heading("#0", text="IP")
        self.user_tree.heading("name", text="昵称")
        self.user_tree.heading("latency", text="延迟")
        self.user_tree.column("#0", width=110, anchor="w")
        self.user_tree.column("name", width=80, anchor="w")
        self.user_tree.column("latency", width=60, anchor="e")
        self.user_tree.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)

        # 聊天记录
        chat_frame = ttk.LabelFrame(top_paned, text="聊天记录")
        top_paned.add(chat_frame, weight=3)
        self.chat_text = scrolledtext.ScrolledText(
            chat_frame, wrap=tk.WORD, state=tk.DISABLED, font=("Microsoft YaHei", 10)
        )
        self.chat_text.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)

        # 下方
        bottom_frame = ttk.Frame(main_paned)
        main_paned.add(bottom_frame, weight=1)

        # 游戏房间
        game_frame = ttk.LabelFrame(bottom_frame, text="游戏房间")
        game_frame.pack(fill=tk.X, padx=3, pady=3)

        game_btn_row = ttk.Frame(game_frame)
        game_btn_row.pack(fill=tk.X, padx=3, pady=(3, 0))

        ttk.Button(game_btn_row, text="+ 创建房间", command=self._create_room).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(game_btn_row, text="加入房间", command=self._join_room).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(game_btn_row, text="结束自己的房间", command=self._stop_room).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(game_btn_row, text="刷新", command=self._refresh_rooms_cmd).pack(side=tk.LEFT, padx=(0, 5))

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
        self.game_tree.column("host", width=120)
        self.game_tree.column("addr", width=220)
        self.game_tree.pack(fill=tk.X, padx=3, pady=3)
        self.game_tree.bind("<Double-1>", self._on_room_double_click)

        # 语音控制栏
        voice_frame = ttk.Frame(bottom_frame)
        voice_frame.pack(fill=tk.X, padx=3, pady=3)

        settings_btn = ttk.Button(voice_frame, text="设置", command=self._open_settings_dialog)
        settings_btn.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Radiobutton(
            voice_frame, text="自由发言 (VAD)",
            variable=self.voice_mode, value="vad",
            command=self._on_mode_change
        ).pack(side=tk.LEFT, padx=3)
        ttk.Radiobutton(
            voice_frame, text="按键说话 (PTT)",
            variable=self.voice_mode, value="ptt",
            command=self._on_mode_change
        ).pack(side=tk.LEFT, padx=3)

        self.hotkey_label = ttk.Label(
            voice_frame,
            text="热键: Ctrl+Shift+V",
            foreground="gray",
            cursor="hand2",
        )
        self.hotkey_label.pack(side=tk.LEFT, padx=(5, 3))
        self.hotkey_label.bind("<Button-1>", lambda e: self._on_hotkey_click())

        ttk.Label(voice_frame, text="麦克风音量:").pack(side=tk.LEFT, padx=(20, 3))
        self.mic_volume = tk.Scale(
            voice_frame, from_=0, to=100, orient=tk.HORIZONTAL, length=120,
            command=self._on_volume_change
        )
        self.mic_volume.set(80)
        self.mic_volume.pack(side=tk.LEFT)

        self.voice_btn = ttk.Button(
            voice_frame, text="加入语音", command=self._toggle_voice
        )
        self.voice_btn.pack(side=tk.RIGHT, padx=3)

        # 输入框行
        input_frame = ttk.Frame(bottom_frame)
        input_frame.pack(fill=tk.X, padx=3, pady=(0, 5))
        self.input_entry = ttk.Entry(input_frame)
        self.input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.input_entry.bind("<Return>", lambda e: self._on_send())

        ttk.Button(input_frame, text="📎 发送文件", command=self._on_send_file).pack(side=tk.RIGHT, padx=(5, 0))
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
        self.event_bus.subscribe(Events.FILE_PROGRESS, self._on_file_progress)
        self.event_bus.subscribe("peer_latency", self._on_peer_latency)

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

    def _on_file_progress(self, data):
        self.ui_queue.put(("file_progress", data))

    def _on_peer_latency(self, data):
        self.ui_queue.put(("peer_latency", data))

    # ========== UI 队列 ==========
    def _poll_ui_queue(self):
        try:
            while True:
                kind, data = self.ui_queue.get_nowait()
                self._apply_ui_update(kind, data)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_ui_queue)

    def _schedule_ui(self, callback, *args, **kwargs):
        """从非主线程安全地调度 UI 操作到主线程"""
        try:
            self.root.after(0, lambda: callback(*args, **kwargs))
        except Exception:
            try:
                callback(*args, **kwargs)
            except Exception:
                pass

    # ========== 系统托盘 ==========
    def _setup_tray(self):
        if pystray is None or Image is None:
            return
        try:
            icon_image = Image.new("RGB", (64, 64), "#1E90FF")
            self.tray_icon = pystray.Icon(
                "MajesticLink",
                icon_image,
                "MajesticLink",
                menu=pystray.Menu(
                    pystray.MenuItem(
                        "显示窗口",
                        lambda: self._schedule_ui(self._restore_from_tray_impl),
                    ),
                    pystray.MenuItem(
                        "加入语音/退出语音",
                        lambda: self._schedule_ui(self._toggle_voice_from_tray_impl),
                    ),
                    pystray.MenuItem(
                        "设置",
                        lambda: self._schedule_ui(self._open_settings_dialog),
                    ),
                    pystray.MenuItem(
                        "退出",
                        lambda: self._schedule_ui(self._on_close),
                    ),
                ),
            )
            self.tray_icon.run_detached()
        except Exception:
            self.tray_icon = None

    def _show_notification(self, title: str, message: str):
        """通过托盘气泡发送通知（不阻塞）"""
        if not self.is_minimized:
            return
        if self.tray_icon is not None:
            try:
                self.tray_icon.notify(message, title)
                return
            except Exception:
                pass
        # 兜底：控制台打印
        print(f"[通知] {title}: {message}")

    def _minimize_to_tray(self):
        if self.tray_icon is not None:
            self.is_minimized = True
            self.root.withdraw()
            return
        self._on_close()

    def _restore_from_tray_impl(self):
        self.is_minimized = False
        self.root.deiconify()
        self.root.state("normal")
        self.root.focus_force()

    def _toggle_voice_from_tray_impl(self):
        self.event_bus.publish("user_toggle_voice")

    # ========== @提及检测 ==========
    def _is_mention(self, content: str) -> bool:
        if not content:
            return False
        mention = f"@{self.current_username}"
        return mention.lower() in content.lower()

    def _mention_span(self, content: str):
        if not content:
            return None
        mention = f"@{self.current_username}"
        lower = content.lower()
        idx = lower.find(mention.lower())
        if idx == -1:
            return None
        return idx, idx + len(mention)

    # ========== UI 更新 ==========
    def _apply_ui_update(self, kind: str, data):
        if kind == "peer_joined":
            name = data.get("name", "?")
            ip = data.get("ip", "")
            if not ip or ip in self._peer_items:
                return
            item_id = self.user_tree.insert(
                "", tk.END, text=ip, values=(name, "测量中...")
            )
            self._peer_items[ip] = item_id

        elif kind == "peer_left":
            ip = data.get("ip", "")
            item_id = self._peer_items.pop(ip, None)
            if item_id:
                self.user_tree.delete(item_id)

        elif kind == "chat_received":
            sender = data.get("sender", "?")
            content = data.get("content", "")
            t = data.get("time")
            time_str = time.strftime("%H:%M:%S", time.localtime(t)) if t else ""
            self._append_chat(
                f"[{time_str}] [{sender}] {content}",
                mention=self._is_mention(content),
            )
            if sender != self.current_username and self.is_minimized:
                self._show_notification(f"消息来自 {sender}", content)

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

        elif kind == "file_progress":
            tid = data.get("transfer_id", "")
            progress = data.get("progress", 0)
            print(f"[File] {tid} 进度 {progress}%")

        elif kind == "peer_latency":
            ip = data.get("ip", "")
            rtt_ms = data.get("rtt_ms", -1)
            item_id = self._peer_items.get(ip)
            if item_id:
                latency_str = f"{rtt_ms:.0f} ms" if rtt_ms >= 0 else "?"
                values = self.user_tree.item(item_id, "values")
                name = values[0] if values else "?"
                self.user_tree.item(item_id, values=(name, latency_str))

    def _refresh_latency(self):
        if self.control:
            try:
                for ip, item_id in list(self._peer_items.items()):
                    rtt = self.control.get_latency(ip)
                    if rtt >= 0:
                        values = self.user_tree.item(item_id, "values")
                        name = values[0] if values else "?"
                        self.user_tree.item(item_id, values=(name, f"{rtt:.0f} ms"))
            except Exception:
                pass
        self.root.after(1000, self._refresh_latency)

    def _append_chat(self, text: str, mention: bool = False):
        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.tag_config(
            "mention",
            background="#fff2a8",
            foreground="#7a2d00",
            font=("Microsoft YaHei", 10, "bold"),
        )
        if mention:
            span = self._mention_span(text)
            if span is None:
                self.chat_text.insert(tk.END, text)
            else:
                start, end = span
                self.chat_text.insert(tk.END, text[:start])
                self.chat_text.insert(tk.END, text[start:end], "mention")
                self.chat_text.insert(tk.END, text[end:])
        else:
            self.chat_text.insert(tk.END, text)
        self.chat_text.insert(tk.END, "\n")
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
                    f"{room.get('ip')}:{room.get('port')}",
                ),
            )

    # ========== 文字 ==========
    def _on_send(self):
        text = self.input_entry.get().strip()
        if not text:
            return
        self.input_entry.delete(0, tk.END)
        self.event_bus.publish("user_send_message", {"content": text})

    # ========== 发送文件 ==========
    def _on_send_file(self):
        target = self._pick_target_user()
        if not target:
            return
        file_path = filedialog.askopenfilename(title="选择要发送的文件")
        if not file_path:
            return
        self.event_bus.publish("user_send_file", {
            "target_name": target,
            "file_path": file_path,
        })

    def _pick_target_user(self) -> Optional[str]:
        items = self.user_tree.get_children()
        if not items:
            self._append_chat("[系统] 当前没有其他在线用户")
            return None

        peers = []
        for item_id in items:
            ip = self.user_tree.item(item_id, "text")
            values = self.user_tree.item(item_id, "values")
            name = values[0] if values else "?"
            peers.append((ip, name))

        if len(peers) == 1:
            ip, name = peers[0]
            if messagebox.askyesno("确认", f"发送给 {name} ？"):
                return name
            return None

        dialog = tk.Toplevel(self.root)
        dialog.title("选择接收者")
        dialog.geometry("300x320")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="选择接收者:").pack(pady=5)
        lb = tk.Listbox(dialog)
        lb.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        for ip, name in peers:
            lb.insert(tk.END, f"{name} ({ip})")

        result = {"name": None}

        def on_ok():
            sel = lb.curselection()
            if sel:
                result["name"] = peers[sel[0]][1]
            dialog.destroy()

        btn_row = ttk.Frame(dialog)
        btn_row.pack(pady=10)
        ttk.Button(btn_row, text="确定", command=on_ok, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row, text="取消", command=dialog.destroy, width=8).pack(side=tk.LEFT, padx=5)
        lb.bind("<Double-1>", lambda e: on_ok())

        self.root.wait_window(dialog)
        return result["name"]

    # ========== 游戏房间 ==========
    def _create_room(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("创建游戏房间")
        dialog.geometry("320x220")
        dialog.transient(self.root)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="游戏名称:").grid(row=0, column=0, sticky=tk.W, pady=6)
        name_var = tk.StringVar(value="minecraft")
        ttk.Entry(frame, textvariable=name_var, width=22).grid(row=0, column=1, pady=6)

        ttk.Label(frame, text="端口:").grid(row=1, column=0, sticky=tk.W, pady=6)
        port_var = tk.StringVar(value="25565")
        ttk.Entry(frame, textvariable=port_var, width=22).grid(row=1, column=1, pady=6)

        ttk.Label(
            frame,
            text="提示: 我的世界默认端口 25565\n泰拉瑞亚默认端口 7777",
            foreground="gray",
            font=("Microsoft YaHei", 8),
        ).grid(row=2, column=0, columnspan=2, pady=6)

        result = {"ok": False}

        def on_ok():
            game_name = name_var.get().strip()
            port_str = port_var.get().strip()
            if not game_name:
                return
            try:
                int(port_str)
            except ValueError:
                return
            result["ok"] = True
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=3, column=0, columnspan=2, pady=10)
        ttk.Button(btn_row, text="创建", command=on_ok, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row, text="取消", command=on_cancel, width=8).pack(side=tk.LEFT, padx=5)

        self.root.wait_window(dialog)

        if result["ok"]:
            game_name = name_var.get().strip()
            port = port_var.get().strip()
            self.event_bus.publish("user_send_message", {
                "content": f"/host {game_name} {port}"
            })

    def _join_room(self):
        items = self.game_tree.get_children()
        if not items:
            self._append_chat("[系统] 当前没有可加入的房间")
            return

        rooms = []
        for item in items:
            values = self.game_tree.item(item, "values")
            rooms.append(values[0])

        dialog = tk.Toplevel(self.root)
        dialog.title("加入房间")
        dialog.geometry("260x280")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="选择要加入的房间:").pack(pady=5)
        lb = tk.Listbox(dialog)
        lb.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        for r in rooms:
            lb.insert(tk.END, r)
        if rooms:
            lb.selection_set(0)

        def on_ok():
            sel = lb.curselection()
            if sel:
                chosen = rooms[sel[0]]
                dialog.destroy()
                self.event_bus.publish("user_send_message", {
                    "content": f"/join {chosen}"
                })
            else:
                dialog.destroy()

        btn_row = ttk.Frame(dialog)
        btn_row.pack(pady=10)
        ttk.Button(btn_row, text="加入", command=on_ok, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row, text="取消", command=dialog.destroy, width=8).pack(side=tk.LEFT, padx=5)
        lb.bind("<Double-1>", lambda e: on_ok())

        self.root.wait_window(dialog)

    def _stop_room(self):
        self.event_bus.publish("user_send_message", {"content": "/rooms"})
        self._append_chat("[提示] 输入 /stopgame <游戏名> 来结束你的房间")

    def _refresh_rooms_cmd(self):
        self.event_bus.publish("user_send_message", {"content": "/rooms"})

    def _on_room_double_click(self, event):
        item = self.game_tree.identify_row(event.y)
        if not item:
            return
        values = self.game_tree.item(item, "values")
        if len(values) >= 3:
            addr = values[2]
            self.root.clipboard_clear()
            self.root.clipboard_append(addr)
            self._append_chat(f"[系统] 已复制地址到剪贴板: {addr}")

    # ========== 语音控制 ==========
    def _toggle_voice(self):
        self.event_bus.publish("user_toggle_voice")

    def _apply_saved_voice_config(self):
        if not self.config:
            return
        mode = str(self.config.get("voice_mode", "vad")).strip().lower()
        if mode in {"vad", "ptt"}:
            self.voice_mode.set(mode)
        try:
            volume = float(self.config.get("voice_volume", "80"))
            self.mic_volume.set(max(0, min(100, int(volume))))
        except Exception:
            pass

        key = str(self.config.get("ptt_key", self._current_ptt_key)).strip() or self._current_ptt_key
        self._current_ptt_key = key.lower()
        self._refresh_hotkey_label()

    def _refresh_hotkey_label(self):
        display = "+".join(p.capitalize() for p in self._current_ptt_key.split("+"))
        self.hotkey_label.config(text=f"热键: {display}")

    def _persist_config(self, key: str, value: object):
        if not self.config:
            return
        self.config[key] = str(value)
        if self.config_callback is not None:
            try:
                self.config_callback(self.config)
            except Exception:
                pass

    def _open_settings_dialog(self):
        from gui.config_dialog import ConfigDialog

        current = self.config.copy() if self.config else {}
        dialog = ConfigDialog(
            default_name=current.get("network_name", "MajesticLink"),
            default_secret=current.get("network_secret", "majesticlink-default-secret"),
            default_username=current.get("username", ""),
            default_room_password=current.get("room_password", ""),
            default_room_id=current.get("room_id", ""),
            master=self.root,
        )
        result = dialog.show()
        if result is None:
            return

        old_network_name = str(current.get("network_name", "")).strip()
        old_network_secret = str(current.get("network_secret", "")).strip()
        new_network_name = str(result.get("network_name", "")).strip()
        new_network_secret = str(result.get("network_secret", "")).strip()

        self.config.update(result)
        self.config.setdefault("room_password", current.get("room_password", ""))
        self.config.setdefault("room_id", current.get("room_id", ""))
        self.config.setdefault("voice_volume", current.get("voice_volume", "80"))
        self.config.setdefault("voice_mode", current.get("voice_mode", "vad"))
        self.config.setdefault("ptt_key", current.get("ptt_key", "ctrl+shift+v"))

        if self.config_callback is not None:
            try:
                self.config_callback(self.config)
            except Exception:
                pass

        if (old_network_name != new_network_name) or (old_network_secret != new_network_secret):
            messagebox.showinfo(
                "配置已保存",
                "已更新网络配置，EasyTier 连接参数会在下一次重启程序后生效。\n请重启 MajesticLink 后再继续使用。"
            )

        self.current_username = str(self.config.get("username", "Anonymous")).strip() or "Anonymous"
        if self.config.get("username"):
            self.event_bus.publish(Events.SYSTEM_MESSAGE, {
                "content": f"[设置] 已更新配置：昵称={self.config['username']}，网络={self.config.get('network_name')}"
            })

    def _on_volume_change(self, value):
        try:
            vol = float(value)
        except Exception:
            return
        self._persist_config("voice_volume", int(vol))
        self.event_bus.publish("user_set_volume", {"volume": vol})

    def _on_mode_change(self):
        mode = self.voice_mode.get()
        self._persist_config("voice_mode", mode)
        self.event_bus.publish("user_set_voice_mode", {"mode": mode})

    def _on_hotkey_click(self):
        result = simpledialog.askstring(
            "修改 PTT 热键",
            "输入新热键（组合键用 + 连接，例如 ctrl+shift+v）:",
            initialvalue=self._current_ptt_key,
            parent=self.root,
        )
        if not result:
            return
        key = result.strip().lower()
        if not key:
            return
        self._current_ptt_key = key
        self._persist_config("ptt_key", key)
        self._refresh_hotkey_label()
        self.event_bus.publish("user_set_ptt_key", {"key": key})

    # ========== 关闭 ==========
    def _on_close(self):
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        self.event_bus.publish(Events.APP_QUIT)
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()