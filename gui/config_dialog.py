"""
启动配置对话框：替代控制台 input()
在 --windowed 模式下弹窗询问网络名、密码、昵称。
"""
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox


class ConfigDialog:
    def __init__(self, default_name="MajesticLink",
                 default_secret="majesticlink-default-secret",
                 default_username=""):
        self.result = None
        self.default_name = default_name
        self.default_secret = default_secret
        self.default_username = default_username

    def show(self) -> dict | None:
        """显示对话框，返回配置 dict 或 None（用户取消）"""
        self.root = tk.Tk()
        self.root.title("MajesticLink 配置")
        self.root.geometry("420x260")
        self.root.resizable(False, False)

        # 居中显示
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"+{x}+{y}")

        frame = ttk.Frame(self.root, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        # 网络名称
        ttk.Label(frame, text="网络名称:").grid(row=0, column=0, sticky=tk.W, pady=8)
        self.name_var = tk.StringVar(value=self.default_name)
        ttk.Entry(frame, textvariable=self.name_var, width=32).grid(row=0, column=1, pady=8)

        # 网络密码
        ttk.Label(frame, text="网络密码:").grid(row=1, column=0, sticky=tk.W, pady=8)
        self.secret_var = tk.StringVar(value=self.default_secret)
        ttk.Entry(frame, textvariable=self.secret_var, width=32).grid(row=1, column=1, pady=8)

        # 昵称
        ttk.Label(frame, text="你的昵称:").grid(row=2, column=0, sticky=tk.W, pady=8)
        self.username_var = tk.StringVar(value=self.default_username)
        ttk.Entry(frame, textvariable=self.username_var, width=32).grid(row=2, column=1, pady=8)

        # 提示
        hint = ttk.Label(
            frame,
            text="同一群组内，网络名称和密码必须完全一致",
            foreground="gray",
            font=("Microsoft YaHei", 8)
        )
        hint.grid(row=3, column=0, columnspan=2, pady=(5, 10))

        # 按钮
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=10)

        ttk.Button(btn_frame, text="确定", command=self._on_ok, width=10).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="取消", command=self._on_cancel, width=10).pack(side=tk.LEFT, padx=10)

        self.root.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.root.bind("<Return>", lambda e: self._on_ok())

        # 焦点定位到昵称输入框
        self.root.after(100, lambda: self.root.focus_force())

        self.root.mainloop()
        return self.result

    def _on_ok(self):
        name = self.name_var.get().strip() or self.default_name
        secret = self.secret_var.get().strip() or self.default_secret
        username = self.username_var.get().strip() or "Anonymous"
        self.result = {
            "network_name": name,
            "network_secret": secret,
            "username": username,
        }
        self.root.destroy()

    def _on_cancel(self):
        self.result = None
        self.root.destroy()