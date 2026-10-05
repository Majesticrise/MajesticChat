"""
MajesticLink 入口
阶段 5：控制通道 + 文字聊天 + 文件传输 + 游戏房间 + 语音通话 + 全局热键 + 延迟显示
"""
import asyncio
import os
import sys
import threading
import time
import traceback

from core.config import APP_NAME, APP_VERSION
from core.event_bus import EventBus, Events
from features.chat_manager import ChatManager
from features.file_manager import FileManager
from features.game_manager import GameManager
from features.voice_manager import VoiceManager
from gui.main_window import MainWindow
from network.control_channel import ControlChannel
from network.crypto import Crypto
from network.discovery import Discovery
from network.easytier_manager import EasyTierManager
from network.peer_manager import PeerManager
from runtime.async_runtime import AsyncRuntime
from storage.database import Database


def ask_config() -> dict:
    from gui.config_dialog import ConfigDialog

    has_stdin = False
    try:
        has_stdin = sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        has_stdin = False

    if has_stdin:
        print("\n" + "=" * 50)
        print(f"  {APP_NAME} v{APP_VERSION}")
        print("=" * 50)
        network_name = input("网络名称 (回车使用默认 MajesticLink): ").strip() or "MajesticLink"
        network_secret = input("网络密码 (回车使用默认): ").strip() or "majesticlink-default-secret"
        username = input("你的昵称 (回车使用 Anonymous): ").strip() or "Anonymous"
        return {
            "network_name": network_name,
            "network_secret": network_secret,
            "username": username,
        }

    dialog = ConfigDialog(
        default_name="MajesticLink",
        default_secret="majesticlink-default-secret",
        default_username="",
    )
    result = dialog.show()
    if result is None:
        sys.exit(0)
    return result


def main():
    cfg = ask_config()

    easytier = EasyTierManager()
    runtime = None
    control = None
    voice_mgr = None
    window = None

    try:
        # ---- EasyTier ----
        if not easytier.start(cfg["network_name"], cfg["network_secret"]):
            print("❌ EasyTier 启动失败")
            return

        self_ip = None
        for i in range(15):
            self_ip = easytier.get_self_ip()
            if self_ip:
                print(f"✅ 获取到虚拟 IP: {self_ip}")
                break
            print(f"⏳ 等待虚拟 IP... ({i + 1}/15)")
            time.sleep(3)

        if not self_ip:
            print("❌ 无法获取虚拟 IP，退出")
            return

        # ---- 运行时 ----
        event_bus = EventBus()
        runtime = AsyncRuntime()
        runtime.start()

        # ---- 组件 ----
        peer_manager = PeerManager()
        discovery = Discovery(easytier, peer_manager, event_bus)
        crypto = Crypto(cfg["network_secret"])
        control = ControlChannel(cfg["username"], crypto, event_bus)
        db = Database()
        chat = ChatManager(cfg["username"], control, db, event_bus, runtime)
        file_mgr = FileManager(cfg["username"], control, event_bus, runtime)
        game_mgr = GameManager(cfg["username"], self_ip, control, event_bus)
        voice_mgr = VoiceManager(cfg["username"], self_ip, control, runtime, event_bus)

        # 底层发现新节点 → 主动连接（只有 IP 小的一方发起，避免连接对冲）
        def _ip_less(a: str, b: str) -> bool:
            try:
                ta = tuple(int(x) for x in a.split("."))
                tb = tuple(int(x) for x in b.split("."))
                return ta < tb
            except Exception:
                return a < b

        def on_peer_discovered(data):
            ip = data.get("ip")
            if not ip or ip == self_ip:
                return
            if not _ip_less(self_ip, ip):
                return
            loop = runtime.loop
            if loop is None:
                print("[Main] AsyncRuntime 未启动，跳过连接")
                return
            asyncio.run_coroutine_threadsafe(
                control.connect_to_peer(ip), loop
            )

        event_bus.subscribe(Events.PEER_DISCOVERED, on_peer_discovered)

        # 游戏房间广播：GUI 线程 → 后台 asyncio
        def on_game_broadcast(data):
            loop = runtime.loop
            if loop is None:
                print("[Main] AsyncRuntime 未启动，跳过广播")
                return
            asyncio.run_coroutine_threadsafe(
                control.broadcast(data), loop
            )

        event_bus.subscribe("game_broadcast_rooms", on_game_broadcast)

        # ---- 异步初始化（每个模块独立异常隔离，避免一个崩溃拖垮全部）----
        async def async_setup():
            async def safe_start(name, coro):
                try:
                    await coro
                    print(f"[Main] {name} 已启动")
                except Exception as e:
                    print(f"[Main] {name} 启动失败: {e}")
                    traceback.print_exc()

            await safe_start("ControlServer", control.start_server())
            await safe_start("ChatManager", chat.start())
            await safe_start("FileManager", file_mgr.start())
            await safe_start("GameManager", game_mgr.start())
            await safe_start("VoiceManager", voice_mgr.start())

            asyncio.create_task(discovery.start())
            print("[Main] 系统就绪")

        loop = runtime.loop
        if loop is None:
            raise RuntimeError("AsyncRuntime 未启动，无法初始化后台服务")
        asyncio.run_coroutine_threadsafe(async_setup(), loop)

        # ---- GUI ----
        event_bus.publish(
            Events.SYSTEM_MESSAGE,
            {"content": f"已上线 · 昵称: {cfg['username']} · IP: {self_ip}"}
        )

        # 把 control 传给 GUI，用于查询延迟
        window = MainWindow(event_bus, control=control)

        # ===== GUI 创建后，补发已连接用户事件（防止时序错过）=====
        def _republish_connected_peers():
            time.sleep(2.0)   # 等 GUI 完全就绪
            try:
                for ip, conn in list(control.connections.items()):
                    if conn.handshake_done and conn.username:
                        event_bus.publish(
                            Events.PEER_JOINED,
                            {"ip": ip, "name": conn.username}
                        )
                        print(f"[Main] 补发 PEER_JOINED: {conn.username} ({ip})")
            except Exception as e:
                print(f"[Main] 补发用户事件失败: {e}")

        threading.Thread(target=_republish_connected_peers, daemon=True).start()

        window.run()

    except Exception as e:
        print(f"运行异常: {e}")
        traceback.print_exc()

    finally:
        print("[Main] 正在清理资源...")
        try:
            if voice_mgr and runtime and runtime.loop:
                asyncio.run_coroutine_threadsafe(
                    voice_mgr.stop(), runtime.loop
                ).result(timeout=3)
        except Exception:
            pass

        try:
            if control and runtime and runtime.loop:
                asyncio.run_coroutine_threadsafe(
                    control.stop(), runtime.loop
                ).result(timeout=3)
        except Exception:
            pass

        try:
            if runtime:
                runtime.stop()
        except Exception:
            pass

        try:
            easytier.stop()
        except Exception:
            pass

        print("[Main] 已退出")


if __name__ == "__main__":
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"启动失败: {e}")
        traceback.print_exc()
        try:
            import tkinter.messagebox as mb
            mb.showerror("MajesticLink 启动失败", f"{e}\n\n详细日志见 error.log")
        except Exception:
            pass