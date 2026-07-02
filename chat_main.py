import asyncio
import configparser
import os
import secrets
import sqlite3
import sys
import time
import traceback

import chat_config
from chat_config import APP_VERSION, BASE_DIR, CONFIG_DIR, log_error
from chat_node import ChatNode
from easytier_utils import start_easytier, stop_easytier, run_cli, get_self_ip, get_peer_ips, easytier_proc

NETWORK_NAME = ""
NETWORK_SECRET = ""


async def main():
    global NETWORK_NAME, NETWORK_SECRET

    if sys.platform == "win32":
        try:
            import ctypes
            if not ctypes.windll.shell32.IsUserAnAdmin():
                log_error("⚠️  建议以管理员身份运行本程序")
        except Exception:
            pass

    config = configparser.ConfigParser()
    config_path = os.path.join(BASE_DIR, "config.ini")
    default_name = "MajesticChatServer"
    default_secret = ""
    default_username = ""

    def save_config(path: str, name: str, secret: str, user: str):
        config['Chat'] = {
            'network_name': name,
            'network_secret': secret,
            'username': user,
        }
        try:
            with open(path, 'w', encoding='utf-8') as f:
                config.write(f)
            print(f"✅ 配置已保存到 {path}")
        except Exception as e:
            log_error(f"保存配置文件失败: {e}")

    if os.path.exists(config_path):
        config.read(config_path, encoding='utf-8')
        if 'Chat' in config:
            cfg_name = config['Chat'].get('network_name', '').strip()
            cfg_secret = config['Chat'].get('network_secret', '').strip()
            cfg_user = config['Chat'].get('username', '').strip()
            if cfg_name and cfg_user:
                NETWORK_NAME = cfg_name
                NETWORK_SECRET = cfg_secret or os.environ.get('MAJESTICCHAT_SECRET', '')
                username = cfg_user
                if not NETWORK_SECRET:
                    NETWORK_SECRET = secrets.token_hex(8)
                    save_config(config_path, NETWORK_NAME, NETWORK_SECRET, username)
                print("\n" + "=" * 50)
                print("          去中心化 P2P 聊天室")
                print(f"版本: {APP_VERSION}")
                print("=" * 50)
                print(f"✅ 使用配置文件: 网络={NETWORK_NAME}, 用户={username}")
                await start_networking_and_chat(username)
                return

    print("\n" + "=" * 50)
    print("          去中心化 P2P 聊天室")
    print(f"版本: {APP_VERSION}")
    print("=" * 50)
    print("首次使用，请配置网络信息：")
    NETWORK_NAME = input(f"请输入网络名称 (直接回车使用默认: {default_name}): ").strip()
    if not NETWORK_NAME:
        NETWORK_NAME = default_name
    NETWORK_SECRET = input(f"请输入网络密码 (直接回车使用默认: 自动生成): ").strip()
    if not NETWORK_SECRET:
        NETWORK_SECRET = secrets.token_hex(8)
    username = input(f"请输入你的昵称 (直接回车使用: {default_username or '匿名用户'}): ").strip()
    if not username:
        username = default_username or "匿名用户"

    save_config(config_path, NETWORK_NAME, NETWORK_SECRET, username)
    await start_networking_and_chat(username)


async def start_networking_and_chat(username: str):
    print(f"✅ 网络: {NETWORK_NAME}, 密码: {NETWORK_SECRET[:3]}***")
    print("正在启动组网服务...\n")

    start_easytier(NETWORK_NAME, NETWORK_SECRET, CONFIG_DIR)

    self_ip = None
    for attempt in range(15):
        self_ip = get_self_ip()
        if self_ip:
            print(f"✅ 获取到虚拟 IP: {self_ip}")
            break
        print(f"⏳ 等待虚拟 IP 分配... ({attempt + 1}/15)")
        time.sleep(3)

    if not self_ip:
        cli_output = run_cli("peer")
        log_error(f"❌ 无法获取本机虚拟 IP，'easytier-cli peer' 输出:\n{cli_output}")
        if easytier_proc and easytier_proc.poll() is None:
            log_error("⚠️  easytier-core 进程仍在运行")
        stop_easytier()
        sys.exit(1)

    db_path = os.path.join(BASE_DIR, "chat_history.db")
    db_conn = sqlite3.connect(db_path, check_same_thread=False)
    db_conn.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time REAL NOT NULL,
            sender TEXT,
            content TEXT,
            msg_id TEXT UNIQUE,
            type TEXT
        )
    ''')
    cutoff = time.time() - 180 * 24 * 3600
    db_conn.execute('DELETE FROM messages WHERE time < ?', (cutoff,))
    db_conn.commit()

    node = ChatNode(username, self_ip, db_conn, NETWORK_SECRET)
    server = await node.start_server()

    asyncio.create_task(node.refresh_peers())

    initial_peers = []
    from easytier_utils import get_peer_ips
    for ip in get_peer_ips():
        if ip != self_ip:
            asyncio.create_task(node.connect_to_peer(ip))

    asyncio.create_task(node.user_input_loop())

    print("\n💬 聊天已开始，输入消息按回车发送。按 Ctrl+C 退出。")
    print("   /list 或 /who 查看在线用户，/history 查看最近聊天记录，/msg <昵称> <内容> 私聊")

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        node.running = False
        server.close()
        await server.wait_closed()
        stop_easytier()
        if db_conn:
            db_conn.close()
        await node.shutdown()


def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 退出聊天室")
        stop_easytier()
    except Exception as e:
        log_error(f"主程序异常: {e}\n{traceback.format_exc()}")
        input("按回车键退出...")


if __name__ == "__main__":
    run()
