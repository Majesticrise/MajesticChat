import os
import sys
import time
import traceback

APP_VERSION = "1.0.1"
NETWORK_NAME = ""
NETWORK_SECRET = ""

# ========== 设置 Windows 控制台为 UTF-8 ==========
if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleCP(65001)
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = open(sys.stdout.fileno(), 'w', encoding='utf-8', errors='replace')
        sys.stderr = open(sys.stderr.fileno(), 'w', encoding='utf-8', errors='replace')

# ========== 日志记录 ==========
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")


def log_error(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{timestamp}] {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(full_msg + "\n")
    except Exception:
        pass
    print(full_msg)


def global_exception_handler(exc_type, exc_value, exc_tb):
    log_error("未捕获的异常:\n" + "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = global_exception_handler


# ========== 终端检测 ==========
def check_terminal():
    if sys.platform == "win32":
        is_wt = os.environ.get("WT_SESSION") is not None
        is_ps = "powershell" in os.environ.get("SHELL", "").lower() or \
                "powershell" in os.environ.get("TERM_PROGRAM", "").lower()
        if not is_wt and not is_ps:
            print("⚠️  当前终端可能无法显示 Emoji 表情，建议使用 Windows Terminal 或 PowerShell 运行。")
            print("   您可以安装 Windows Terminal: https://aka.ms/terminal\n")


# ========== 配置 ==========
PORT = 8888
REFRESH_INTERVAL = 15
CONNECT_TIMEOUT = 5
HEARTBEAT_INTERVAL = 30
SYNC_BATCH_SIZE = 50
RELAY_PEERS = os.environ.get("EASYTIER_PEERS", "tcp://38.147.105.178:11010")
LISTEN_HOST = os.environ.get("MAJESTICCHAT_BIND_HOST", "0.0.0.0")


# ========== 路径 ==========
def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()
CORE_EXE = os.path.join(BASE_DIR, "easytier-core.exe")
CLI_EXE = os.path.join(BASE_DIR, "easytier-cli.exe")
CONFIG_DIR = os.path.join(BASE_DIR, ".easytier")
