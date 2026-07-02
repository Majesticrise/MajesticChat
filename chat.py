import asyncio
import base64
import configparser
import concurrent.futures
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import traceback
from typing import Any, Dict, Mapping, Optional, Set

APP_VERSION = "1.0.1"

# ========== 设置 Windows 控制台为 UTF-8 ==========
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleCP(65001)
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except:
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
    except:
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
SYNC_BATCH_SIZE = 50   # 每批同步消息数
RELAY_PEERS = os.environ.get("EASYTIER_PEERS", "tcp://38.147.105.178:11010")
LISTEN_HOST = os.environ.get("MAJESTICCHAT_BIND_HOST", "0.0.0.0")

# ========== 路径 ==========
def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()
CORE_EXE = os.path.join(BASE_DIR, "easytier-core.exe")
CLI_EXE = os.path.join(BASE_DIR, "easytier-cli.exe")
CONFIG_DIR = os.path.join(BASE_DIR, ".easytier")

# ========== 启动检查 ==========
def check_files():
    missing = []
    if not os.path.exists(CORE_EXE):
        missing.append("easytier-core.exe")
    if not os.path.exists(CLI_EXE):
        missing.append("easytier-cli.exe")
    if missing:
        log_error(f"❌ 缺少必需文件: {', '.join(missing)}")
        log_error(f"请确保这些文件与 {os.path.basename(sys.executable)} 位于同一目录")
        return False
    return True

# ========== EasyTier 控制 ==========
easytier_proc = None
NETWORK_NAME = ""
NETWORK_SECRET = ""

def start_easytier(network_name: str, network_secret: str, config_dir: str):
    global easytier_proc
    if not check_files():
        sys.exit(1)
    if not network_name or not network_secret:
        log_error("❌ 网络名或密码为空，请重新启动并输入")
        sys.exit(1)
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/f", "/im", "easytier-core.exe"],
                           capture_output=True, encoding='utf-8', errors='ignore', timeout=2)
        except:
            pass
    os.makedirs(config_dir, exist_ok=True)
    cmd = [
        CORE_EXE,
        "--network-name", network_name,
        "--network-secret", network_secret,
        "--config-dir", config_dir,
        "--peers", RELAY_PEERS,
        "--use-smoltcp",
        "--dhcp"
    ]
    try:
        easytier_proc = subprocess.Popen(cmd, creationflags=0)
        print("✅ EasyTier 核心服务已启动，正在组网...")
        time.sleep(4)
        if easytier_proc.poll() is not None:
            log_error(f"❌ EasyTier 进程意外退出，返回码 {easytier_proc.returncode}")
            sys.exit(1)
        else:
            print("✅ EasyTier 进程运行中")
    except Exception as e:
        log_error(f"❌ 启动 EasyTier 失败: {e}\n{traceback.format_exc()}")
        sys.exit(1)

def stop_easytier():
    global easytier_proc
    if easytier_proc:
        try:
            easytier_proc.terminate()
            easytier_proc.wait(timeout=5)
        except:
            pass
        print("🛑 EasyTier 已停止")

def run_cli(*args):
    cmd = [CLI_EXE] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='ignore', timeout=10)
        if result.stderr:
            log_error(f"easytier-cli stderr: {result.stderr}")
        return result.stdout
    except Exception as e:
        log_error(f"运行 easytier-cli 时出错: {e}")
        return ""

def get_self_ip() -> Optional[str]:
    output = run_cli("peer")
    if not output:
        return None
    match = re.search(r'(\d+\.\d+\.\d+\.\d+)\s*\(self\)', output)
    if match:
        return match.group(1)
    lines = output.splitlines()
    header_line = None
    for line in lines:
        if 'ipv4' in line and 'hostname' in line:
            header_line = line
            break
    if header_line:
        parts = header_line.split('|')
        col_idx = None
        for i, p in enumerate(parts):
            if 'ipv4' in p:
                col_idx = i
                break
        if col_idx is not None:
            for line in lines:
                if 'Local' in line or 'LAPTOP' in line:
                    cols = line.split('|')
                    if len(cols) > col_idx:
                        val = cols[col_idx].strip()
                        if val and val != '-':
                            if '/' in val:
                                val = val.split('/')[0]
                            return val
    return None

def is_valid_virtual_ip(ip: str) -> bool:
    """只保留 EasyTier 虚拟网段（10.0.0.0/8 或 100.64.0.0/10）"""
    if ip.startswith('10.'):
        return True
    if ip.startswith('100.64.'):
        return True
    return False

def get_peer_ips() -> Set[str]:
    output = run_cli("peer")
    ips = set()
    for line in output.splitlines():
        if '(self)' in line:
            continue
        m = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
        if m:
            ip = m.group(1)
            if is_valid_virtual_ip(ip):
                ips.add(ip)
    return ips

# ========== 聊天核心 ==========
class ChatNode:
    def __init__(self, username: str, self_ip: str, db_conn, network_secret: str):
        self.username = username
        self.self_ip = self_ip
        self.db_conn = db_conn
        self.network_secret = network_secret or ""
        self.connections: Dict[str, asyncio.StreamWriter] = {}
        self.readers: Dict[str, asyncio.StreamReader] = {}
        self.peer_names: Dict[str, str] = {}
        self.lock = asyncio.Lock()
        self.running = True
        self.known_peer_ips = set()
        self.reconnect_tasks = {}
        self.last_msg_id = 0
        self.last_msg_id_lock = asyncio.Lock()   # 保护 last_msg_id
        self.heartbeat_tasks: Dict[str, asyncio.Task] = {}
        self.last_ping_time: Dict[str, float] = {}
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        # 文件传输相关
        self.file_receive_contexts: Dict[str, dict] = {}  # key: f"{ip}:{transfer_id}"
        self.file_send_tasks: Dict[str, asyncio.Task] = {}  # key: transfer_id
        self.file_cancel_flags: Dict[str, bool] = {}       # key: transfer_id
        self.file_receive_lock = asyncio.Lock()
        self.file_send_lock = asyncio.Lock()
        
        # 文件接收超时任务
        self.file_receive_timeouts: Dict[str, asyncio.Task] = {}  # key: f"{ip}:{transfer_id}"
        
        # 心跳超时检测任务
        self.heartbeat_timeout_task: Optional[asyncio.Task] = None
        self.session_keys: Dict[str, bytes] = {}
        self.local_nonces: Dict[str, str] = {}
        self.peer_nonces: Dict[str, str] = {}
        self.private_msg_counter = 0

        # 初始化 last_msg_id
        cursor = db_conn.execute('SELECT MAX(id) FROM messages')
        row = cursor.fetchone()
        if row and row[0]:
            self.last_msg_id = row[0]

        self.heartbeat_timeout_task = asyncio.create_task(self._heartbeat_timeout_checker())


    async def _execute_db(self, sql, params=()):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, self.db_conn.execute, sql, params)

    async def _commit_db(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self.executor, self.db_conn.commit)

    @staticmethod
    def _as_str(value: object, default: str = "") -> str:
        return value if isinstance(value, str) else default

    @staticmethod
    def _as_float(value: object, default: Optional[float] = None) -> Optional[float]:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return default

    @staticmethod
    def _as_int(value: object, default: int = 0) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        return default

    async def start_server(self):
        try:
            server = await asyncio.start_server(
                self.handle_client, LISTEN_HOST, PORT
            )
            print(f"📡 聊天服务器已启动，监听端口 {PORT}")
            return server
        except Exception as e:
            log_error(f"启动服务器失败: {e}\n{traceback.format_exc()}")
            raise

    async def handle_client(self, reader, writer):
        addr = writer.get_extra_info('peername')
        ip = addr[0] if addr else "unknown"
        async with self.lock:
            if ip in self.connections:
                writer.close()
                await writer.wait_closed()
                return
            self.connections[ip] = writer
            self.readers[ip] = reader
        print(f"🔗 新连接: {ip}")
        self.last_ping_time[ip] = time.time()
        await self.send_handshake(writer, ip)
        await asyncio.sleep(0.5)
        # 启动心跳并存储任务
        task = asyncio.create_task(self._heartbeat_sender(ip))
        self.heartbeat_tasks[ip] = task
        asyncio.create_task(self.send_sync_request(ip))
        await self.receive_messages(reader, ip)

    async def connect_to_peer(self, ip: str):
        if ip == self.self_ip:
            return
        async with self.lock:
            if ip in self.connections:
                return
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, PORT),
                timeout=CONNECT_TIMEOUT
            )
            async with self.lock:
                self.connections[ip] = writer
                self.readers[ip] = reader
            print(f"🔗 主动连接 {ip} 成功")
            self.last_ping_time[ip] = time.time()
            await self.send_handshake(writer, ip)
            await asyncio.sleep(0.5)
            task = asyncio.create_task(self._heartbeat_sender(ip))
            self.heartbeat_tasks[ip] = task
            asyncio.create_task(self.send_sync_request(ip))
            asyncio.create_task(self.receive_messages(reader, ip))
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 连接 {ip} 失败: {e}\n")

    async def _heartbeat_timeout_checker(self):
        """后台任务：检查心跳超时，超时则断开连接"""
        while self.running:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            now = time.time()
            to_close = []
            async with self.lock:
                for ip, last_ping in list(self.last_ping_time.items()):
                    if now - last_ping > 2 * HEARTBEAT_INTERVAL:
                        to_close.append(ip)
            for ip in to_close:
                print(f"[系统] 心跳超时，断开 {ip}")
                await self._close_connection(ip)

    async def _heartbeat_sender(self, ip: str):
        """定期发送心跳"""
        while self.running:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            async with self.lock:
                if ip not in self.connections:
                    break
                writer = self.connections[ip]
            try:
                await self._send_json(writer, {"type": "ping"}, ip)
                self.last_ping_time[ip] = time.time()
            except Exception:
                # 发送失败，断开
                await self._close_connection(ip)
                break

    async def _close_connection(self, ip: str):
        # 先清理文件接收上下文（需要 file_receive_lock）
        async with self.file_receive_lock:
            keys_to_delete = []
            for key, ctx in list(self.file_receive_contexts.items()):
                if key.startswith(f"{ip}:"):
                    if ctx["file_handle"] and not ctx["file_handle"].closed:
                        ctx["file_handle"].close()
                    if os.path.exists(ctx["target_path"]):
                        os.remove(ctx["target_path"])
                    keys_to_delete.append(key)
            for key in keys_to_delete:
                del self.file_receive_contexts[key]
                if key in self.file_receive_timeouts:
                    task = self.file_receive_timeouts[key]
                    if not task.done():
                        task.cancel()
                    del self.file_receive_timeouts[key]
        # 再清理连接相关（需要 self.lock）
        async with self.lock:
            if ip in self.connections:
                writer = self.connections[ip]
                try:
                    writer.close()
                    await writer.wait_closed()
                except:
                    pass
                del self.connections[ip]
            if ip in self.readers:
                del self.readers[ip]
            if ip in self.peer_names:
                del self.peer_names[ip]
            if ip in self.heartbeat_tasks:
                task = self.heartbeat_tasks[ip]
                if not task.done():
                    task.cancel()
                del self.heartbeat_tasks[ip]
            if ip in self.last_ping_time:
                del self.last_ping_time[ip]
        print(f"🔌 连接断开: {ip}")

    def _derive_session_key(self, peer_nonce: str, local_nonce: str) -> bytes:
        if not self.network_secret:
            return b""
        ordered_nonces = ":".join(sorted([local_nonce, peer_nonce]))
        return hashlib.sha256(f"{self.network_secret}:{ordered_nonces}".encode("utf-8")).digest()

    def _wrap_payload(self, payload: Mapping[str, Any], peer_ip: str) -> Optional[Dict[str, Any]]:
        if not self.network_secret:
            return None
        key = self.session_keys.get(peer_ip)
        if not key:
            return None
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        nonce = secrets.token_hex(8)
        masked = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
        mac = hmac.new(key, masked + nonce.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "type": "secure",
            "nonce": nonce,
            "payload": base64.b64encode(masked).decode("ascii"),
            "mac": mac,
        }

    def _unwrap_payload(self, msg: Mapping[str, Any], peer_ip: str) -> Optional[Dict[str, Any]]:
        if not self.network_secret:
            return None
        key = self.session_keys.get(peer_ip)
        if not key:
            return None
        payload_b64 = msg.get("payload")
        nonce = msg.get("nonce")
        mac = msg.get("mac")
        if not isinstance(payload_b64, str) or not isinstance(nonce, str) or not isinstance(mac, str):
            return None
        try:
            masked = base64.b64decode(payload_b64.encode("ascii"))
        except Exception:
            return None
        expected_mac = hmac.new(key, masked + nonce.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_mac, mac):
            return None
        raw = bytes(b ^ key[i % len(key)] for i, b in enumerate(masked))
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    async def _send_json(self, writer, payload: Mapping[str, Any], peer_ip: str):
        wrapped = self._wrap_payload(payload, peer_ip)
        if wrapped is not None:
            writer.write((json.dumps(wrapped) + "\n").encode("utf-8"))
        else:
            writer.write((json.dumps(payload) + "\n").encode("utf-8"))
        await writer.drain()

    async def send_handshake(self, writer, peer_ip: str):
        local_nonce = secrets.token_hex(8)
        self.local_nonces[peer_ip] = local_nonce
        payload = {
            "type": "handshake",
            "username": self.username,
            "nonce": local_nonce,
            "auth": hmac.new(
                self.network_secret.encode("utf-8"),
                f"{self.username}:{local_nonce}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest() if self.network_secret else "",
        }
        writer.write((json.dumps(payload) + "\n").encode('utf-8'))
        await writer.drain()
        peer_nonce = self.peer_nonces.get(peer_ip)
        if peer_nonce:
            self.session_keys[peer_ip] = self._derive_session_key(peer_nonce, local_nonce)

    async def send_sync_request(self, peer_ip: str):
        """同步请求携带 last_msg_id"""
        print(f"[系统] 正在与 {peer_ip} 同步历史消息...")
        async with self.last_msg_id_lock:
            last_id = self.last_msg_id
        async with self.lock:
            if peer_ip not in self.connections:
                return
            writer = self.connections[peer_ip]
        req = {"type": "sync_req", "last_msg_id": last_id}
        try:
            await self._send_json(writer, req, peer_ip)
        except Exception as e:
            log_error(f"发送同步请求到 {peer_ip} 失败: {e}")

    async def receive_messages(self, reader, ip):
        try:
            while self.running:
                data = await reader.readline()
                if not data:
                    break
                raw = data.decode('utf-8', errors='replace').strip()
                try:
                    parsed = json.loads(raw)
                    if not isinstance(parsed, dict):
                        print(f"[错误] 无法解析JSON对象，原始数据: {raw[:100]}")
                        continue
                    msg: Dict[str, Any] = parsed
                    if self._as_str(msg.get("type"), "") == "secure":
                        unwrapped = self._unwrap_payload(msg, ip)
                        if unwrapped is None:
                            print(f"[警告] 收到无效的安全消息，来自 {ip}")
                            await self._close_connection(ip)
                            break
                        msg = unwrapped
                    msg_type = self._as_str(msg.get("type"), "")
                    if msg_type == "ping":
                        # 回应 pong
                        pong = json.dumps({"type": "pong"})
                        async with self.lock:
                            if ip in self.connections:
                                writer = self.connections[ip]
                                await self._send_json(writer, {"type": "pong"}, ip)
                    elif msg_type == "pong":
                        # 收到 pong，更新最后心跳时间
                        self.last_ping_time[ip] = time.time()

                    elif msg_type == "file_start":
                        sender = self._as_str(msg.get("sender"), "Unknown")
                        filename = self._as_str(msg.get("filename"), "unknown_file")
                        total_size = self._as_int(msg.get("total_size"), 0)
                        total_chunks = self._as_int(msg.get("total_chunks"), 0)
                        transfer_id = self._as_str(msg.get("transfer_id"), "")
                        file_md5 = self._as_str(msg.get("md5"), "")
                        if not transfer_id:
                            print(f"\n[系统] 文件传输缺少传输ID，已忽略")
                            return
                        print(f"\n[系统] 接收到来自 {sender} 的文件: {filename} ({total_size} 字节)，共 {total_chunks} 块")
                        # 准备接收
                        save_dir = os.path.join(BASE_DIR, "received_files")
                        os.makedirs(save_dir, exist_ok=True)
                        base, ext = os.path.splitext(filename)
                        counter = 1
                        save_path = os.path.join(save_dir, filename)
                        while os.path.exists(save_path):
                            save_path = os.path.join(save_dir, f"{base}_{counter}{ext}")
                            counter += 1
                        f = open(save_path, 'wb')
                        context = {
                            "filename": filename,
                            "total_chunks": total_chunks,
                            "received": 0,
                            "file_handle": f,
                            "target_path": save_path,
                            "sender": sender,
                            "transfer_id": transfer_id,
                            "md5": file_md5,
                            "last_progress": -1
                        }
                        key = f"{ip}:{transfer_id}"
                        async with self.file_receive_lock:
                            self.file_receive_contexts[key] = context
                        # 启动接收超时任务（300秒）
                        timeout_task = asyncio.create_task(self._file_receive_timeout(key, 300))
                        async with self.file_receive_lock:
                            self.file_receive_timeouts[key] = timeout_task

                    elif msg_type == "file_chunk":
                        sender = self._as_str(msg.get("sender"), "Unknown")
                        transfer_id = self._as_str(msg.get("transfer_id"), "")
                        if not transfer_id:
                            return
                        key = f"{ip}:{transfer_id}"
                        async with self.file_receive_lock:
                            context = self.file_receive_contexts.get(key)
                        if not context:
                            print(f"\n[系统] 收到未预期的文件块，来自 {sender}，传输ID {transfer_id}，已忽略")
                            return
                        try:
                            import base64
                            data_b64 = msg.get("data")
                            if not isinstance(data_b64, str):
                                raise ValueError("缺少文件块数据")
                            chunk_data = base64.b64decode(data_b64)
                            context["file_handle"].write(chunk_data)
                            context["received"] += 1
                            # 进度
                            total = context["total_chunks"]
                            received = context["received"]
                            progress = int(received * 100 / total)
                            if progress >= context["last_progress"] + 10:
                                print(f"\r[系统] 接收进度: {progress}% ({received}/{total})", end='')
                                context["last_progress"] = progress
                            if received >= total:
                                context["file_handle"].close()
                                # 校验MD5
                                if context["md5"]:
                                    # 计算接收文件的MD5
                                    md5 = hashlib.md5()
                                    with open(context["target_path"], 'rb') as f:
                                        for chunk in iter(lambda: f.read(8192), b''):
                                            md5.update(chunk)
                                    if md5.hexdigest() == context["md5"]:
                                        print(f"\n[系统] 文件 '{context['filename']}' 已接收完成，保存为 {context['target_path']} (校验通过)")
                                    else:
                                        print(f"\n[系统] 文件 '{context['filename']}' 校验失败，可能已损坏")
                                        os.remove(context["target_path"])
                                else:
                                    print(f"\n[系统] 文件 '{context['filename']}' 已接收完成，保存为 {context['target_path']}")
                                # 清理上下文
                                async with self.file_receive_lock:
                                    if key in self.file_receive_contexts:
                                        del self.file_receive_contexts[key]
                        except Exception as e:
                            log_error(f"接收文件块失败: {e}")
                            if context["file_handle"] and not context["file_handle"].closed:
                                context["file_handle"].close()
                            # 删除未完成的文件
                            if os.path.exists(context["target_path"]):
                                os.remove(context["target_path"])
                            async with self.file_receive_lock:
                                if key in self.file_receive_contexts:
                                    del self.file_receive_contexts[key]
                            print(f"\n[系统] 文件接收失败: {e}")

                    elif msg_type == "file_abort":
                        sender = self._as_str(msg.get("sender"), "Unknown")
                        transfer_id = self._as_str(msg.get("transfer_id"), "")
                        key = f"{ip}:{transfer_id}"
                        async with self.file_receive_lock:
                            if key in self.file_receive_contexts:
                                ctx = self.file_receive_contexts[key]
                                if ctx["file_handle"] and not ctx["file_handle"].closed:
                                    ctx["file_handle"].close()
                                if os.path.exists(ctx["target_path"]):
                                    os.remove(ctx["target_path"])
                                del self.file_receive_contexts[key]
                        print(f"\n[系统] 文件传输已取消（来自 {sender}）")
                    
                    
                    elif msg_type == "handshake":
                        name = self._as_str(msg.get("username"), "Unknown")
                        peer_nonce = self._as_str(msg.get("nonce"), "")
                        auth_value = self._as_str(msg.get("auth"), "")
                        if self.network_secret and peer_nonce:
                            expected_auth = hmac.new(
                                self.network_secret.encode("utf-8"),
                                f"{name}:{peer_nonce}".encode("utf-8"),
                                hashlib.sha256,
                            ).hexdigest()
                            if not hmac.compare_digest(expected_auth, auth_value):
                                print(f"[警告] 收到来自 {ip} 的无效握手，已拒绝")
                                await self._close_connection(ip)
                                break
                        if peer_nonce:
                            self.peer_nonces[ip] = peer_nonce
                        local_nonce = self.local_nonces.get(ip, "")
                        if self.network_secret and peer_nonce and local_nonce:
                            self.session_keys[ip] = self._derive_session_key(peer_nonce, local_nonce)
                        async with self.lock:
                            is_new = ip not in self.peer_names
                            self.peer_names[ip] = name
                        if is_new:
                            await self.broadcast_system(f"👋 {name} 加入了聊天室", exclude_ip=ip)
                            print(f"[系统] {name} 加入了聊天室")
                    elif msg_type == "chat":
                        sender = self._as_str(msg.get("sender"), "Unknown")
                        content = self._as_str(msg.get("content"), "")
                        t = self._as_float(msg.get("time"))
                        msg_id = self._as_str(msg.get("msg_id"), "")
                        if t is not None:
                            local_time = time.strftime("%H:%M:%S", time.localtime(t))
                            print(f"\n[{local_time}] {sender}: {content}")
                        else:
                            print(f"\n[{sender}] {content}")
                        if msg_id:
                            try:
                                await self._execute_db(
                                    'INSERT OR IGNORE INTO messages (time, sender, content, msg_id, type) VALUES (?, ?, ?, ?, ?)',
                                    (t, sender, content, msg_id, 'chat')
                                )
                                await self._commit_db()
                                # 更新 last_msg_id
                                async with self.last_msg_id_lock:
                                    try:
                                        num_id = int(msg_id.split('_')[-1])
                                        if num_id > self.last_msg_id:
                                            self.last_msg_id = num_id
                                    except:
                                        pass
                            except Exception as e:
                                log_error(f"保存聊天消息到数据库失败: {e}")
                    elif msg_type == "system":
                        content = self._as_str(msg.get("content"), "")
                        print(f"\n[系统] {content}")
                    elif msg_type == "sync_req":
                        last_id = self._as_int(msg.get("last_msg_id"), 0)
                        try:
                            # 分页查询，每批 SYNC_BATCH_SIZE 条
                            offset = 0
                            while True:
                                cursor = await self._execute_db(
                                    'SELECT time, sender, content, msg_id FROM messages WHERE type="chat" AND id > ? ORDER BY id ASC LIMIT ? OFFSET ?',
                                    (last_id, SYNC_BATCH_SIZE, offset)
                                )
                                rows = cursor.fetchall()
                                if not rows:
                                    break
                                for row in rows:
                                    sync_msg = {
                                        "type": "sync_msg",
                                        "time": row[0],
                                        "sender": row[1],
                                        "content": row[2],
                                        "msg_id": row[3]
                                    }
                                    async with self.lock:
                                        if ip not in self.connections:
                                            return
                                        writer = self.connections[ip]
                                    try:
                                        await self._send_json(writer, sync_msg, ip)
                                    except Exception as e:
                                        log_error(f"发送同步消息到 {ip} 失败: {e}")
                                        return
                                offset += SYNC_BATCH_SIZE
                                await asyncio.sleep(0.02)  # 控制流控
                            # 发送结束标记
                            end_msg = {"type": "sync_end"}
                            async with self.lock:
                                if ip in self.connections:
                                    writer = self.connections[ip]
                                    try:
                                        await self._send_json(writer, end_msg, ip)
                                    except:
                                        pass
                        except Exception as e:
                            log_error(f"处理同步请求异常: {e}")
                    
                    elif msg_type == "private":
                        sender = self._as_str(msg.get("sender"), "Unknown")
                        content = self._as_str(msg.get("content"), "")
                        t = self._as_float(msg.get("time"))
                        if t is not None:
                            local_time = time.strftime("%H:%M:%S", time.localtime(t))
                            print(f"\n[{local_time}] [私聊] {sender}: {content}")
                        else:
                            print(f"\n[私聊] {sender}: {content}")
                        # 保存到数据库（接收方也需要存储）
                        try:
                            await self._execute_db(
                                'INSERT OR IGNORE INTO messages (time, sender, content, msg_id, type) VALUES (?, ?, ?, ?, ?)',
                                (t, sender, content, f"private_{int(time.time()*1000)}", 'private')
                            )
                            await self._commit_db()
                        except Exception as e:
                            log_error(f"保存私聊消息失败: {e}")

                    elif msg_type == "sync_msg":
                        t = self._as_float(msg.get("time"))
                        sender = self._as_str(msg.get("sender"), "Unknown")
                        content = self._as_str(msg.get("content"), "")
                        msg_id = self._as_str(msg.get("msg_id"), "")
                        if msg_id:
                            try:
                                await self._execute_db(
                                    'INSERT OR IGNORE INTO messages (time, sender, content, msg_id, type) VALUES (?, ?, ?, ?, ?)',
                                    (t, sender, content, msg_id, 'chat')   # 统一存为 chat
                                )
                                await self._commit_db()
                                async with self.last_msg_id_lock:
                                    try:
                                        num_id = int(msg_id.split('_')[-1])
                                        if num_id > self.last_msg_id:
                                            self.last_msg_id = num_id
                                    except:
                                        pass
                            except Exception as e:
                                log_error(f"保存同步消息到数据库失败: {e}")
                    elif msg_type == "sync_end":
                        print(f"[系统] 与 {ip} 同步完成")
                    else:
                        print(f"[警告] 未知消息类型: {msg_type}")
                except json.JSONDecodeError:
                    print(f"[错误] 无法解析JSON，原始数据: {raw[:100]}")
                except Exception as e:
                    print(f"[错误] 处理消息时异常: {e}")
                    log_error(f"处理消息异常 ({ip}): {e}\n{traceback.format_exc()}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log_error(f"接收消息异常 ({ip}): {e}\n{traceback.format_exc()}")
        finally:
            await self._close_connection(ip)

    async def _file_receive_timeout(self, key: str, timeout_seconds: int):
        """接收超时检测，超时则清理上下文并发送 file_abort（如果连接还在）"""
        await asyncio.sleep(timeout_seconds)
        async with self.file_receive_lock:
            if key not in self.file_receive_contexts:
                return
            ctx = self.file_receive_contexts[key]
            # 如果接收已完成（received >= total_chunks），则无需超时处理
            if ctx["received"] >= ctx["total_chunks"]:
                if key in self.file_receive_timeouts:
                    del self.file_receive_timeouts[key]
                return
            # 超时，清理资源
            if ctx["file_handle"] and not ctx["file_handle"].closed:
                ctx["file_handle"].close()
            if os.path.exists(ctx["target_path"]):
                os.remove(ctx["target_path"])
            # 提取 ip 和 transfer_id
            ip, transfer_id = key.split(':', 1)
            # 尝试发送 file_abort
            abort_msg = {
                "type": "file_abort",
                "sender": self.username,
                "transfer_id": transfer_id
            }
            async with self.lock:
                if ip in self.connections:
                    writer = self.connections[ip]
                    try:
                        await self._send_json(writer, abort_msg, ip)
                    except:
                        pass
            del self.file_receive_contexts[key]
            if key in self.file_receive_timeouts:
                del self.file_receive_timeouts[key]
            print(f"\n[系统] 文件接收超时 ({timeout_seconds}秒)，已取消")

    async def reconnect_peer(self, ip: str):
        backoff = 1
        for attempt in range(3):
            await asyncio.sleep(backoff)
            async with self.lock:
                if ip in self.connections:
                    return
            try:
                await self.connect_to_peer(ip)
                return
            except Exception:
                backoff = min(backoff * 2, 30)
        async with self.lock:
            if ip in self.reconnect_tasks:
                del self.reconnect_tasks[ip]

    async def broadcast_system(self, content: str, exclude_ip: str | None = None):
        payload = {"type": "system", "content": content}
        async with self.lock:
            peers = [(peer_ip, writer) for peer_ip, writer in list(self.connections.items()) if peer_ip != exclude_ip]
        for peer_ip, writer in peers:
            try:
                await self._send_json(writer, payload, peer_ip)
            except Exception:
                pass
        print(f"\n[系统] {content}")

    async def broadcast(self, content: str):
        t = time.time()
        msg_num = int(t * 1000)
        msg_id = f"{self.username}_{msg_num}"
        msg = {
            "type": "chat",
            "sender": self.username,
            "content": content,
            "time": t,
            "msg_id": msg_id,
        }
        async with self.lock:
            writers = list(self.connections.items())
        for peer_ip, writer in writers:
            try:
                await self._send_json(writer, msg, peer_ip)
            except Exception as e:
                log_error(f"向 {writer.get_extra_info('peername')} 发送失败: {e}")
        # 保存自己消息
        try:
            await self._execute_db(
                'INSERT OR IGNORE INTO messages (time, sender, content, msg_id, type) VALUES (?, ?, ?, ?, ?)',
                (t, self.username, content, msg_id, 'chat')
            )
            await self._commit_db()
            async with self.last_msg_id_lock:
                if msg_num > self.last_msg_id:
                    self.last_msg_id = msg_num
        except Exception as e:
            log_error(f"保存自己消息到数据库失败: {e}")

    async def refresh_peers(self):
        while self.running:
            await asyncio.sleep(REFRESH_INTERVAL)
            try:
                peers = get_peer_ips()
                peers.discard(self.self_ip)
                async with self.lock:
                    current = set(self.connections.keys())
                new_peers = peers - current
                for ip in new_peers:
                    self.known_peer_ips.add(ip)
                    asyncio.create_task(self.connect_to_peer(ip))
            except Exception as e:
                log_error(f"刷新对等节点时出错: {e}\n{traceback.format_exc()}")

    async def user_input_loop(self):
        """使用 asyncio.to_thread 直接阻塞读取 stdin，无空转"""
        loop = asyncio.get_running_loop()
        while self.running:
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                if line.startswith('/'):
                    await self.handle_command(line)
                else:
                    await self.broadcast(line)
            except Exception as e:
                log_error(f"处理用户输入异常: {e}\n{traceback.format_exc()}")

    async def _send_file(self, target_ip: str, target_name: str, file_path: str, transfer_id: str):
        """发送文件（带总超时300秒，支持取消）"""
        try:
            # 设置总超时 300 秒
            await asyncio.wait_for(
                self._send_file_internal(target_ip, target_name, file_path, transfer_id),
                timeout=300
            )
        except asyncio.TimeoutError:
            print(f"\n[系统] 文件发送超时 (300秒)")
            # 尝试发送取消消息
            await self._send_abort(target_ip, transfer_id)
        except asyncio.CancelledError:
            print(f"\n[系统] 文件发送被取消")
            # 若已发送 file_start，通知接收端取消
            await self._send_abort(target_ip, transfer_id)
        except Exception as e:
            log_error(f"发送文件失败: {e}")
            print(f"\n[系统] 发送文件失败: {e}")
        finally:
            # 清理发送任务
            async with self.file_send_lock:
                if transfer_id in self.file_send_tasks:
                    del self.file_send_tasks[transfer_id]
                if transfer_id in self.file_cancel_flags:
                    del self.file_cancel_flags[transfer_id]

    async def _send_file_internal(self, target_ip: str, target_name: str, file_path: str, transfer_id: str):
        """实际的发送逻辑（不含超时包装）"""
        file_size = os.path.getsize(file_path)
        filename = os.path.basename(file_path)
        chunk_size = 64 * 1024  # 64KB
        total_chunks = (file_size + chunk_size - 1) // chunk_size

        # 计算MD5
        md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5.update(chunk)
        file_md5 = md5.hexdigest()

        # 发送开始消息
        start_msg = {
            "type": "file_start",
            "sender": self.username,
            "filename": filename,
            "total_size": file_size,
            "total_chunks": total_chunks,
            "transfer_id": transfer_id,
            "md5": file_md5,
        }
        async with self.lock:
            if target_ip not in self.connections:
                print(f"\n[系统] 目标连接已断开")
                return
            writer = self.connections[target_ip]
            await self._send_json(writer, start_msg, target_ip)

        # 分块发送
        sent = 0
        last_progress = -1
        with open(file_path, 'rb') as f:
            for i in range(total_chunks):
                # 检查取消标志
                async with self.file_send_lock:
                    if self.file_cancel_flags.get(transfer_id, False):
                        print(f"\n[系统] 文件传输已取消")
                        await self._send_abort(target_ip, transfer_id)
                        return
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                import base64
                encoded = base64.b64encode(chunk).decode('ascii')
                chunk_msg = {
                    "type": "file_chunk",
                    "sender": self.username,
                    "transfer_id": transfer_id,
                    "index": i,
                    "total": total_chunks,
                    "data": encoded,
                }
                async with self.lock:
                    if target_ip not in self.connections:
                        print(f"\n[系统] 传输中断: 目标断开")
                        return
                    writer = self.connections[target_ip]
                    await self._send_json(writer, chunk_msg, target_ip)
                sent += len(chunk)
                progress = int((i+1) * 100 / total_chunks)
                if progress >= last_progress + 10:
                    print(f"\r[系统] 发送进度: {progress}% ({sent}/{file_size} 字节)", end='')
                    last_progress = progress
                await asyncio.sleep(0.001)
        print(f"\n[系统] 文件 '{filename}' 已发送给 {target_name} ({file_size} 字节)")

    async def _send_abort(self, target_ip: str, transfer_id: str):
        """发送取消通知给接收端"""
        abort_msg = {
            "type": "file_abort",
            "sender": self.username,
            "transfer_id": transfer_id,
        }
        async with self.lock:
            if target_ip in self.connections:
                writer = self.connections[target_ip]
                try:
                    await self._send_json(writer, abort_msg, target_ip)
                except:
                    pass

    async def handle_command(self, cmd: str):
        parts = cmd.split()
        if not parts:
            return
        command = parts[0].lower()
        if command == '/list' or command == '/who':
            async with self.lock:
                peers = list(self.peer_names.values())
                if self.username not in peers:
                    peers.insert(0, self.username)
                if peers:
                    online_str = ", ".join(peers)
                    print(f"\n[系统] 当前在线 {len(peers)} 人: {online_str}")
                else:
                    print(f"\n[系统] 当前只有你在线")
        elif command == '/history':
            try:
                cursor = await self._execute_db(
                    'SELECT time, sender, content, type FROM messages WHERE type="chat" OR type="private" ORDER BY id DESC LIMIT 20'
                )
                rows = cursor.fetchall()
                if not rows:
                    print("\n[系统] 暂无聊天记录。")
                else:
                    print("\n[系统] 最近20条聊天记录：")
                    for t, sender, content, msg_type in reversed(rows):
                        local_time = time.strftime("%H:%M:%S", time.localtime(t))
                        if msg_type == 'private':
                            print(f"{local_time} [私聊] {sender}: {content}")
                        else:
                            print(f"{local_time} [{sender}] {content}")
            except Exception as e:
                log_error(f"查询历史记录失败: {e}")
        elif command == '/msg':
            if len(parts) < 3:
                print("\n[系统] 用法: /msg <昵称> <消息内容>")
                return
            target_name = parts[1]
            content = ' '.join(parts[2:])
            target_ip = None
            async with self.lock:
                for ip, name in self.peer_names.items():
                    if name == target_name:
                        target_ip = ip
                        break
            if target_ip is None:
                print(f"\n[系统] 用户 '{target_name}' 不在线")
                return
            # 构造私聊消息
            t = time.time()
            self.private_msg_counter += 1
            msg = {
                "type": "private",
                "sender": self.username,
                "content": content,
                "time": t,
                "msg_id": f"private_{self.username}_{int(t * 1000)}_{self.private_msg_counter}",
            }
            async with self.lock:
                if target_ip not in self.connections:
                    print(f"\n[系统] 用户 '{target_name}' 连接已断开")
                    return
                writer = self.connections[target_ip]
                try:
                    await self._send_json(writer, msg, target_ip)
                    # 本地显示发送记录
                    local_time = time.strftime("%H:%M:%S", time.localtime(t))
                    print(f"\n[{local_time}] [私聊] 你 -> {target_name}: {content}")
                    # 保存到数据库
                    await self._execute_db(
                        'INSERT OR IGNORE INTO messages (time, sender, content, msg_id, type) VALUES (?, ?, ?, ?, ?)',
                        (t, self.username, content, msg['msg_id'], 'private')
                    )
                    await self._commit_db()
                except Exception as e:
                    log_error(f"发送私聊给 {target_name} 失败: {e}")
                    print(f"\n[系统] 发送私聊失败")
        elif command == '/send':
            if len(parts) < 3:
                print("\n[系统] 用法: /send <昵称> <文件路径>")
                return
            target_name = parts[1]
            file_path = ' '.join(parts[2:])
            if not os.path.exists(file_path):
                print(f"\n[系统] 文件不存在: {file_path}")
                return
            target_ip = None
            async with self.lock:
                for ip, name in self.peer_names.items():
                    if name == target_name:
                        target_ip = ip
                        break
            if target_ip is None:
                print(f"\n[系统] 用户 '{target_name}' 不在线")
                return
            transfer_id = f"{self.username}_{int(time.time()*1000)}"
            # 存储取消标志
            async with self.file_send_lock:
                self.file_cancel_flags[transfer_id] = False
            task = asyncio.create_task(self._send_file(target_ip, target_name, file_path, transfer_id))
            async with self.file_send_lock:
                self.file_send_tasks[transfer_id] = task
            print(f"\n[系统] 开始发送文件到 {target_name}，传输ID: {transfer_id}")
        elif command == '/cancel':
            # 取消发送，格式：/cancel <传输ID> 或 /cancel all
            if len(parts) < 2:
                print("\n[系统] 用法: /cancel <传输ID> 或 /cancel all")
                return
            target = parts[1]
            if target == 'all':
                async with self.file_send_lock:
                    ids = list(self.file_send_tasks.keys())
                for tid in ids:
                    await self._cancel_send(tid)
                print("\n[系统] 已取消所有文件传输")
            else:
                await self._cancel_send(target)
        elif command == '/help':
            print("\n[系统] 可用命令:")
            print("   /list 或 /who 查看在线用户")
            print("   /history 查看最近聊天记录（包含私聊）")
            print("   /msg <昵称> <消息> 发送私聊")
            print("   /send <昵称> <文件路径> 发送文件")
            print("   /cancel <传输ID> 或 /cancel all 取消文件传输")
        else:
            print(f"\n[系统] 未知命令: {command}，输入 /help 查看帮助")

    async def _cancel_send(self, transfer_id: str):
        async with self.file_send_lock:
            if transfer_id not in self.file_send_tasks:
                print(f"\n[系统] 传输ID {transfer_id} 不存在或已完成")
                return
            self.file_cancel_flags[transfer_id] = True
            task = self.file_send_tasks[transfer_id]
            if not task.done():
                task.cancel()
        print(f"\n[系统] 已请求取消传输 {transfer_id}")

    async def shutdown(self):
        self.running = False
        for task in self.heartbeat_tasks.values():
            if not task.done():
                task.cancel()
        self.heartbeat_tasks.clear()
        # 取消所有发送任务
        async with self.file_receive_lock:
            for key, task in list(self.file_receive_timeouts.items()):
                if not task.done():
                    task.cancel()
            self.file_receive_timeouts.clear()
            # 清理文件上下文
            for ctx in self.file_receive_contexts.values():
                if ctx["file_handle"] and not ctx["file_handle"].closed:
                    ctx["file_handle"].close()
                if os.path.exists(ctx["target_path"]):
                    os.remove(ctx["target_path"])
            self.file_receive_contexts.clear()
        # 取消心跳超时检测任务
        if self.heartbeat_timeout_task and not self.heartbeat_timeout_task.done():
            self.heartbeat_timeout_task.cancel()
        # 关闭所有接收文件句柄并删除未完成的文件
        async with self.file_receive_lock:
            for ctx in self.file_receive_contexts.values():
                if ctx["file_handle"] and not ctx["file_handle"].closed:
                    ctx["file_handle"].close()
                if os.path.exists(ctx["target_path"]):
                    os.remove(ctx["target_path"])
            self.file_receive_contexts.clear()
        if self.executor:
            self.executor.shutdown(wait=False)

# ========== 主程序 ==========
async def main():
    global NETWORK_NAME, NETWORK_SECRET


    if sys.platform == "win32":
        try:
            import ctypes
            if not ctypes.windll.shell32.IsUserAnAdmin():
                log_error("⚠️  建议以管理员身份运行本程序")
        except:
            pass

    # 读取/创建配置文件
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
                print("\n" + "="*50)
                print("          去中心化 P2P 聊天室")
                print(f"版本: {APP_VERSION}")
                print("="*50)
                print(f"✅ 使用配置文件: 网络={NETWORK_NAME}, 用户={username}")
                await start_networking_and_chat(username)
                return

    print("\n" + "="*50)
    print("          去中心化 P2P 聊天室")
    print(f"版本: {APP_VERSION}")
    print("="*50)
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
        print(f"⏳ 等待虚拟 IP 分配... ({attempt+1}/15)")
        time.sleep(3)

    if not self_ip:
        cli_output = run_cli("peer")
        log_error(f"❌ 无法获取本机虚拟 IP，'easytier-cli peer' 输出:\n{cli_output}")
        if easytier_proc and easytier_proc.poll() is None:
            log_error("⚠️  easytier-core 进程仍在运行")
        stop_easytier()
        sys.exit(1)

    # 初始化数据库
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

    initial_peers = get_peer_ips()
    for ip in initial_peers:
        if ip != self_ip:
            asyncio.create_task(node.connect_to_peer(ip))

    # 使用 asyncio 的 input 循环（无空转）
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