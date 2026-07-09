import asyncio
import base64
import concurrent.futures
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import traceback
from typing import Any, Dict, Mapping, Optional

from chat_config import BASE_DIR, HEARTBEAT_INTERVAL, LISTEN_HOST, PORT, CONNECT_TIMEOUT, REFRESH_INTERVAL, SYNC_BATCH_SIZE, log_error
from easytier_utils import get_peer_ips


class GameManager:
    def __init__(self):
        self.rooms = {}                 # 游戏名 -> 房间信息（含时间戳）
        self.participants = {}          # 游戏名 -> 参与者用户名集合（Set）
        self.cleanup_interval = 300     # 5分钟超时

    def add_room(self, game_name, host_username, host_ip, port):
        """创建房间，并自动将主机加入参与者集合"""
        self.rooms[game_name] = {
            'host_username': host_username,
            'host_ip': host_ip,
            'port': port,
            'timestamp': time.time()
        }
        # 初始化参与者集合，将主机加入
        self.participants[game_name] = {host_username}

    def remove_room(self, game_name):
        """删除房间及其参与者集合"""
        if game_name in self.rooms:
            del self.rooms[game_name]
        if game_name in self.participants:
            del self.participants[game_name]

    def get_room(self, game_name):
        return self.rooms.get(game_name)

    def list_rooms(self):
        # 清理超时房间（同时清理参与者集合）
        now = time.time()
        expired = [name for name, info in self.rooms.items()
                   if now - info['timestamp'] > self.cleanup_interval]
        for name in expired:
            self.remove_room(name)
        return self.rooms.copy()

    def add_participant(self, game_name, username):
        """将用户加入参与者集合（若房间存在）"""
        if game_name in self.participants:
            self.participants[game_name].add(username)
            return True
        return False

    def remove_participant(self, game_name, username):
        """移除参与者，若移除后集合为空则自动删除房间"""
        if game_name not in self.participants:
            return False
        self.participants[game_name].discard(username)
        if not self.participants[game_name]:
            self.remove_room(game_name)
            return True
        return False

    def get_participants(self, game_name):
        """获取参与者集合（副本）"""
        if game_name in self.participants:
            return self.participants[game_name].copy()
        return set()

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
        self.last_msg_id_lock = asyncio.Lock()
        self.heartbeat_tasks: Dict[str, asyncio.Task] = {}
        self.last_ping_time: Dict[str, float] = {}
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self.file_receive_contexts: Dict[str, dict] = {}
        self.file_send_tasks: Dict[str, asyncio.Task] = {}
        self.file_cancel_flags: Dict[str, bool] = {}
        self.file_receive_lock = asyncio.Lock()
        self.file_send_lock = asyncio.Lock()
        self.file_receive_timeouts: Dict[str, asyncio.Task] = {}
        self.heartbeat_timeout_task: Optional[asyncio.Task] = None
        self.session_keys: Dict[str, bytes] = {}
        self.local_nonces: Dict[str, str] = {}
        self.peer_nonces: Dict[str, str] = {}
        self.private_msg_counter = 0

        cursor = db_conn.execute('SELECT MAX(id) FROM messages')
        row = cursor.fetchone()
        if row and row[0]:
            self.last_msg_id = row[0]

        self.heartbeat_timeout_task = asyncio.create_task(self._heartbeat_timeout_checker())
        self.game_manager = GameManager()

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
            server = await asyncio.start_server(self.handle_client, LISTEN_HOST, PORT)
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
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, PORT), timeout=CONNECT_TIMEOUT)
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
        except asyncio.TimeoutError as e:
            log_error(f"连接 {ip} 超时: {e}")
        except Exception as e:
            log_error(f"连接 {ip} 失败: {e}")

    async def _heartbeat_timeout_checker(self):
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
                await self._close_connection(ip)
                break

    async def _close_connection(self, ip: str):
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
        stop_msgs = []
        async with self.lock:
            host_rooms = [name for name, info in self.game_manager.rooms.items() if info['host_ip'] == ip]
            for name in host_rooms:
                self.game_manager.remove_room(name)
                stop_msgs.append({"type": "game_stop", "game_name": name})
            if ip in self.connections:
                writer = self.connections[ip]
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
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
            peers = list(self.connections.items())
        for stop_msg in stop_msgs:
            for peer_ip, writer in peers:
                try:
                    await self._send_json(writer, stop_msg, peer_ip)
                except Exception:
                    pass
        if stop_msgs:
            for stop_msg in stop_msgs:
                print(f"[游戏] 远程主机 {ip} 的游戏房间 {stop_msg['game_name']} 已结束")
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
        rooms = self.game_manager.list_rooms()
        if rooms:
            room_msg = {"type": "game_list", "rooms": rooms}
            await self._send_json(writer, room_msg, peer_ip)

    async def send_sync_request(self, peer_ip: str):
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
                        async with self.lock:
                            if ip in self.connections:
                                await self._send_json(self.connections[ip], {"type": "pong"}, ip)
                    elif msg_type == "pong":
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
                            data_b64 = msg.get("data")
                            if not isinstance(data_b64, str):
                                raise ValueError("缺少文件块数据")
                            chunk_data = base64.b64decode(data_b64)
                            context["file_handle"].write(chunk_data)
                            context["received"] += 1
                            total = context["total_chunks"]
                            received = context["received"]
                            progress = int(received * 100 / total)
                            if progress >= context["last_progress"] + 10:
                                print(f"\r[系统] 接收进度: {progress}% ({received}/{total})", end='')
                                context["last_progress"] = progress
                            if received >= total:
                                context["file_handle"].close()
                                if context["md5"]:
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
                                async with self.file_receive_lock:
                                    if key in self.file_receive_contexts:
                                        del self.file_receive_contexts[key]
                        except Exception as e:
                            log_error(f"接收文件块失败: {e}")
                            if context["file_handle"] and not context["file_handle"].closed:
                                context["file_handle"].close()
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
                                async with self.last_msg_id_lock:
                                    try:
                                        num_id = int(msg_id.split('_')[-1])
                                        if num_id > self.last_msg_id:
                                            self.last_msg_id = num_id
                                    except Exception:
                                        pass
                            except Exception as e:
                                log_error(f"保存聊天消息到数据库失败: {e}")
                    elif msg_type == "system":
                        content = self._as_str(msg.get("content"), "")
                        print(f"\n[系统] {content}")
                    elif msg_type == "game_host":
                        game_name = self._as_str(msg.get("game_name"), "")
                        host_username = self._as_str(msg.get("host_username"), "")
                        host_ip = self._as_str(msg.get("host_ip"), "")
                        port = self._as_int(msg.get("port"), 0)
                        if game_name and host_username and host_ip and port:
                            self.game_manager.add_room(game_name, host_username, host_ip, port)
                            print(f"\n[游戏] 已同步游戏房间 {game_name}，主机 {host_username} @ {host_ip}:{port}")
                    elif msg_type == "game_list":
                        rooms = msg.get("rooms")
                        if isinstance(rooms, dict):
                            for game_name, info in rooms.items():
                                host_username = self._as_str(info.get("host_username"), "")
                                host_ip = self._as_str(info.get("host_ip"), "")
                                port = self._as_int(info.get("port"), 0)
                                if game_name and host_username and host_ip and port:
                                    self.game_manager.add_room(game_name, host_username, host_ip, port)
                            print(f"\n[游戏] 已同步 {len(rooms)} 个游戏房间")
                    elif msg_type == "game_stop":
                        game_name = self._as_str(msg.get("game_name"), "")
                        if game_name:
                            self.game_manager.remove_room(game_name)
                            print(f"\n[游戏] 游戏房间 {game_name} 已结束")
                    elif msg_type == "sync_req":
                        last_id = self._as_int(msg.get("last_msg_id"), 0)
                        try:
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
                                await asyncio.sleep(0.02)
                            end_msg = {"type": "sync_end"}
                            async with self.lock:
                                if ip in self.connections:
                                    writer = self.connections[ip]
                                    try:
                                        await self._send_json(writer, end_msg, ip)
                                    except Exception:
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
                                    (t, sender, content, msg_id, 'chat')
                                )
                                await self._commit_db()
                                async with self.last_msg_id_lock:
                                    try:
                                        num_id = int(msg_id.split('_')[-1])
                                        if num_id > self.last_msg_id:
                                            self.last_msg_id = num_id
                                    except Exception:
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
        await asyncio.sleep(timeout_seconds)
        async with self.file_receive_lock:
            if key not in self.file_receive_contexts:
                return
            ctx = self.file_receive_contexts[key]
            if ctx["received"] >= ctx["total_chunks"]:
                if key in self.file_receive_timeouts:
                    del self.file_receive_timeouts[key]
                return
            if ctx["file_handle"] and not ctx["file_handle"].closed:
                ctx["file_handle"].close()
            if os.path.exists(ctx["target_path"]):
                os.remove(ctx["target_path"])
            ip, transfer_id = key.split(':', 1)
            abort_msg = {"type": "file_abort", "sender": self.username, "transfer_id": transfer_id}
            async with self.lock:
                if ip in self.connections:
                    writer = self.connections[ip]
                    try:
                        await self._send_json(writer, abort_msg, ip)
                    except Exception:
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
        try:
            await asyncio.wait_for(self._send_file_internal(target_ip, target_name, file_path, transfer_id), timeout=300)
        except asyncio.TimeoutError:
            print(f"\n[系统] 文件发送超时 (300秒)")
            await self._send_abort(target_ip, transfer_id)
        except asyncio.CancelledError:
            print(f"\n[系统] 文件发送被取消")
            await self._send_abort(target_ip, transfer_id)
        except Exception as e:
            log_error(f"发送文件失败: {e}")
            print(f"\n[系统] 发送文件失败: {e}")
        finally:
            async with self.file_send_lock:
                if transfer_id in self.file_send_tasks:
                    del self.file_send_tasks[transfer_id]
                if transfer_id in self.file_cancel_flags:
                    del self.file_cancel_flags[transfer_id]

    async def _send_file_internal(self, target_ip: str, target_name: str, file_path: str, transfer_id: str):
        file_size = os.path.getsize(file_path)
        filename = os.path.basename(file_path)
        chunk_size = 64 * 1024
        total_chunks = (file_size + chunk_size - 1) // chunk_size

        md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5.update(chunk)
        file_md5 = md5.hexdigest()

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

        sent = 0
        last_progress = -1
        with open(file_path, 'rb') as f:
            for i in range(total_chunks):
                async with self.file_send_lock:
                    if self.file_cancel_flags.get(transfer_id, False):
                        print(f"\n[系统] 文件传输已取消")
                        await self._send_abort(target_ip, transfer_id)
                        return
                chunk = f.read(chunk_size)
                if not chunk:
                    break
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
                except Exception:
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
                    local_time = time.strftime("%H:%M:%S", time.localtime(t))
                    print(f"\n[{local_time}] [私聊] 你 -> {target_name}: {content}")
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
            async with self.file_send_lock:
                self.file_cancel_flags[transfer_id] = False
            task = asyncio.create_task(self._send_file(target_ip, target_name, file_path, transfer_id))
            async with self.file_send_lock:
                self.file_send_tasks[transfer_id] = task
            print(f"\n[系统] 开始发送文件到 {target_name}，传输ID: {transfer_id}")
        elif command == '/cancel':
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
        elif command == '/host':
            if len(parts) < 2:
                print("\n[系统] 用法: /host <游戏名> [端口]")
                return
            game_name = parts[1]
            port = 25565
            if len(parts) >= 3:
                try:
                    port = int(parts[2])
                except ValueError:
                    print("\n[系统] 端口必须为数字")
                    return
            if self.game_manager.get_room(game_name):
                print(f"\n[系统] 游戏房间 '{game_name}' 已存在")
                return
            self.game_manager.add_room(game_name, self.username, self.self_ip, port)
            print(f"\n[系统] 你已创建游戏房间 {game_name}，地址 {self.self_ip}:{port}")
            await self.broadcast_system(f"[游戏] {self.username} 已创建游戏房间 {game_name}，地址 {self.self_ip}:{port}")
            host_msg = {
                "type": "game_host",
                "game_name": game_name,
                "host_username": self.username,
                "host_ip": self.self_ip,
                "port": port,
            }
            async with self.lock:
                peers = list(self.connections.items())
            for peer_ip, writer in peers:
                try:
                    await self._send_json(writer, host_msg, peer_ip)
                except Exception:
                    pass
        elif command == '/join':
            if len(parts) < 2:
                print("\n[系统] 用法: /join <游戏名>")
                return
            game_name = parts[1]
            room = self.game_manager.get_room(game_name)
            if not room:
                print(f"\n[系统] 未找到游戏房间 '{game_name}'")
                return
            if self.game_manager.add_participant(game_name, self.username):
                print(f"\n[系统] 你已加入游戏房间 {game_name}，主机 {room['host_username']} @ {room['host_ip']}:{room['port']}")
            else:
                print(f"\n[系统] 无法加入游戏房间 '{game_name}'")
        elif command == '/listgames':
            rooms = self.game_manager.list_rooms()
            if not rooms:
                print("\n[系统] 当前没有活动游戏房间")
            else:
                print("\n[系统] 当前活动游戏房间：")
                for name, info in rooms.items():
                    count = len(self.game_manager.get_participants(name))
                    print(f"   {name} - 主机: {info['host_username']} @ {info['host_ip']}:{info['port']}，参与者: {count}")
        elif command == '/stopgame':
            if len(parts) < 2:
                print("\n[系统] 用法: /stopgame <游戏名>")
                return
            game_name = parts[1]
            room = self.game_manager.get_room(game_name)
            if not room:
                print(f"\n[系统] 未找到游戏房间 '{game_name}'")
                return
            if room['host_username'] != self.username:
                print("\n[系统] 只有房主才能结束游戏房间")
                return
            self.game_manager.remove_room(game_name)
            print(f"\n[系统] 你已结束游戏房间 {game_name}")
            await self.broadcast_system(f"[游戏] {self.username} 结束了 {game_name} 房间")
            stop_msg = {"type": "game_stop", "game_name": game_name}
            async with self.lock:
                peers = list(self.connections.items())
            for peer_ip, writer in peers:
                try:
                    await self._send_json(writer, stop_msg, peer_ip)
                except Exception:
                    pass
        elif command == '/leave':
            if len(parts) < 2:
                print("\n[系统] 用法: /leave <游戏名>")
                return
            game_name = parts[1]
            if not self.game_manager.get_room(game_name):
                print(f"\n[系统] 未找到游戏房间 '{game_name}'")
                return
            emptied = self.game_manager.remove_participant(game_name, self.username)
            if emptied:
                print(f"\n[系统] 你已退出游戏房间 {game_name}，房间已空并关闭")
                await self.broadcast_system(f"[游戏] {self.username} 退出了 {game_name} 房间（房间已空）")
                stop_msg = {"type": "game_stop", "game_name": game_name}
                async with self.lock:
                    peers = list(self.connections.items())
                for peer_ip, writer in peers:
                    try:
                        await self._send_json(writer, stop_msg, peer_ip)
                    except Exception:
                        pass
            else:
                print(f"\n[系统] 你已退出游戏房间 {game_name}")
                await self.broadcast_system(f"[游戏] {self.username} 退出了 {game_name} 房间")
        elif command == '/help':
            print("\n[系统] 可用命令:")
            print("   /list 或 /who 查看在线用户")
            print("   /history 查看最近聊天记录（包含私聊）")
            print("   /msg <昵称> <消息> 发送私聊")
            print("   /send <昵称> <文件路径> 发送文件")
            print("   /cancel <传输ID> 或 /cancel all 取消文件传输")
            print("   /host <游戏名> [端口] 创建游戏房间（你自动成为主机）")
            print("   /join <游戏名> 加入游戏房间（你成为参与者）")
            print("   /leave <游戏名> 退出你加入的游戏房间")
            print("   /listgames 列出所有活动房间")
            print("   /stopgame <游戏名> 结束你作为主机的游戏")
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
        async with self.file_receive_lock:
            for key, task in list(self.file_receive_timeouts.items()):
                if not task.done():
                    task.cancel()
            self.file_receive_timeouts.clear()
            for ctx in self.file_receive_contexts.values():
                if ctx["file_handle"] and not ctx["file_handle"].closed:
                    ctx["file_handle"].close()
                if os.path.exists(ctx["target_path"]):
                    os.remove(ctx["target_path"])
            self.file_receive_contexts.clear()
        if self.heartbeat_timeout_task and not self.heartbeat_timeout_task.done():
            self.heartbeat_timeout_task.cancel()
        async with self.file_receive_lock:
            for ctx in self.file_receive_contexts.values():
                if ctx["file_handle"] and not ctx["file_handle"].closed:
                    ctx["file_handle"].close()
                if os.path.exists(ctx["target_path"]):
                    os.remove(ctx["target_path"])
            self.file_receive_contexts.clear()
        if self.executor:
            self.executor.shutdown(wait=False)
