import os
import sys

APP_NAME = "MajesticLink"
APP_VERSION = "0.1.0"

# ========== 网络配置 ==========
CONTROL_PORT = 8888                # TCP 控制通道端口
VOICE_PORT = 8889                  # UDP 语音通道端口
RELAY_PEERS = os.environ.get(
    "MAJESTICLINK_RELAY",
    "tcp://38.147.105.178:11010"
)
LISTEN_HOST = "0.0.0.0"

# ========== 心跳与超时 ==========
HEARTBEAT_INTERVAL = 30            # 心跳间隔（秒）
HEARTBEAT_TIMEOUT = 90             # 心跳超时（秒）
CONNECT_TIMEOUT = 10               # TCP 连接超时（秒）
READ_TIMEOUT = 60                  # 读超时（秒）
SYNC_BATCH_SIZE = 50               # 历史同步每批条数

# ========== 音频参数 ==========
SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_DURATION_MS = 20
FRAME_SIZE = SAMPLE_RATE * FRAME_DURATION_MS // 1000  # 320
OPUS_BITRATE = 24000
JITTER_BUFFER_MIN_MS = 40
JITTER_BUFFER_MAX_MS = 100
JITTER_BUFFER_DEFAULT_MS = 60

# ========== VAD 参数 ==========
VAD_ENERGY_THRESHOLD = 500
VAD_SILENCE_FRAMES = 15            # 300ms 静音后停止发送

# ========== 路径 ==========
def get_base_dir() -> str:
    """兼容开发与 PyInstaller 打包环境，返回主程序所在目录"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_DIR = get_base_dir()
CORE_EXE = os.path.join(BASE_DIR, "easytier-core.exe")
CLI_EXE = os.path.join(BASE_DIR, "easytier-cli.exe")
CONFIG_DIR = os.path.join(BASE_DIR, ".easytier")
DB_PATH = os.path.join(BASE_DIR, "chat_history.db")
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
RECEIVED_FILES_DIR = os.path.join(BASE_DIR, "received_files")
LOG_FILE = os.path.join(BASE_DIR, "error.log")