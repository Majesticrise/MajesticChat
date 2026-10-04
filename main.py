"""
MajesticLink 入口
阶段 3：控制通道 + 文字聊天
"""
import asyncio
import sys
import time
import traceback

from core.config import APP_NAME, APP_VERSION
from core.event_bus import EventBus, Events
from features.chat_manager import ChatManager
from gui.main_window import MainWindow
from network.control_channel import ControlChannel
from network.crypto import Crypto
from network.discovery import Discovery
from network.easytier_manager import EasyTierManager
from network.peer_manager import PeerManager
from runtime.async_runtime import AsyncRuntime
from storage.database import Database


def ask_config() -> dict:
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


def main():
    cfg = ask_config()

    # ---- EasyTier ----
    easytier = EasyTierManager()
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
        print("❌ 无法获取虚拟 IP")
        easytier.stop()
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
    chat = ChatManager(cfg["username"], control, db, event_bus)

    # 发现新节点 → 主动连接
    def on_peer_joined(data):
        ip = data.get("ip")
        if ip and ip != self_ip:
            asyncio.run_coroutine_threadsafe(
                control.connect_to_peer(ip), runtime.loop
            )
    event_bus.subscribe(Events.PEER_JOINED, on_peer_joined)

    # 握手完成后 → 请求同步
    def on_peer_ready(data):
        pass  # 简化：跳过自动同步，或者在这里触发
    # 简化：ChatManager 自己不会自动同步，需要外界触发
    # 我们在 control 里监听握手完成事件，暂不实现

    # ---- 异步初始化 ----
    async def async_setup():
        await control.start_server()
        await chat.start()
        asyncio.create_task(discovery.start())
        print(f"[Main] 系统就绪")

    asyncio.run_coroutine_threadsafe(async_setup(), runtime.loop)

    # ---- GUI ----
    event_bus.publish(
        Events.SYSTEM_MESSAGE,
        {"content": f"已上线 · 昵称: {cfg['username']} · IP: {self_ip}"}
    )
    window = MainWindow(event_bus)
    window.run()

    # ---- 清理 ----
    print("[Main] 正在退出...")
    try:
        asyncio.run_coroutine_threadsafe(control.stop(), runtime.loop).result(timeout=3)
    except Exception:
        pass
    runtime.stop()
    easytier.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"启动失败: {e}")
        traceback.print_exc()
        input("按回车退出...")