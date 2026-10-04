"""
TCP 控制通道：
- 监听 / 主动连接对等节点
- 握手认证、会话密钥派生
- 心跳保活（Ping/Pong）
- RTT 延迟统计
- 消息分发（通过 EventBus 抛给上层 feature 模块）
"""
import asyncio
import json
import secrets
import time
from typing import Any, Dict, Mapping, Optional

from core.config import (
    CONTROL_PORT, CONNECT_TIMEOUT, READ_TIMEOUT, HEARTBEAT_INTERVAL
)
from core.event_bus import EventBus, Events
from core.protocol import MsgType, make_ping, make_pong
from network.crypto import Crypto


class ConnectionState:
    def __init__(self, ip: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.ip = ip
        self.reader = reader
        self.writer = writer
        self.write_lock = asyncio.Lock()
        self.session_key: Optional[bytes] = None
        self.username: str = ""
        self.local_nonce: str = ""
        self.peer_nonce: str = ""
        self.last_pong = time.time()
        self.is_initiator = False
        self.handshake_done = False

        # ===== 延迟统计 =====
        self.rtt_ms: float = -1.0          # 最近一次 RTT（毫秒）
        self._ping_sent_at: float = 0.0    # 本次 ping 发送时刻


class ControlChannel:
    def __init__(self, username: str, crypto: Crypto, event_bus: EventBus):
        self.username = username
        self.crypto = crypto
        self.event_bus = event_bus
        self.connections: Dict[str, ConnectionState] = {}
        self.server: Optional[asyncio.AbstractServer] = None
        self.running = False
        self._lock = asyncio.Lock()
        self._heartbeat_task: Optional[asyncio.Task] = None

    # ========== 启动 / 停止 ==========
    async def start_server(self):
        self.running = True
        self.server = await asyncio.start_server(
            self._handle_connection, "0.0.0.0", CONTROL_PORT
        )
        print(f"[Control] TCP 服务器监听端口 {CONTROL_PORT}")
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self):
        self.running = False
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        for ip in list(self.connections.keys()):
            await self._disconnect(ip)
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    # ========== 主动连接 ==========
    async def connect_to_peer(self, ip: str):
        async with self._lock:
            if ip in self.connections:
                return
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, CONTROL_PORT),
                timeout=CONNECT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            print(f"[Control] 连接 {ip} 超时")
            return
        except Exception as e:
            print(f"[Control] 连接 {ip} 失败: {e}")
            return

        try:
            reader.set_read_limit(1024 * 1024)
        except AttributeError:
            reader._limit = 1024 * 1024

        conn = ConnectionState(ip, reader, writer)
        conn.is_initiator = True
        async with self._lock:
            self.connections[ip] = conn
        print(f"[Control] 已连接 {ip}")

        await self._send_handshake(conn)
        asyncio.create_task(self._receive_loop(ip))

    # ========== 处理新连接 ==========
    async def _handle_connection(self, reader, writer):
        addr = writer.get_extra_info("peername")
        ip = addr[0] if addr else "unknown"
        async with self._lock:
            if ip in self.connections:
                try:
                    writer.close()
                except Exception:
                    pass
                return
        try:
            reader.set_read_limit(1024 * 1024)
        except AttributeError:
            reader._limit = 1024 * 1024

        conn = ConnectionState(ip, reader, writer)
        conn.is_initiator = False
        async with self._lock:
            self.connections[ip] = conn
        print(f"[Control] 新连接: {ip}")

        await self._send_handshake(conn)
        await self._receive_loop(ip)

    # ========== 握手 ==========
    async def _send_handshake(self, conn: ConnectionState):
        conn.local_nonce = secrets.token_hex(8)
        auth = self.crypto.make_auth(self.username, conn.local_nonce)
        payload = {
            "type": MsgType.HANDSHAKE,
            "username": self.username,
            "nonce": conn.local_nonce,
            "auth": auth,
        }
        await self._raw_send(conn, payload)

    async def _on_handshake(self, msg: Mapping[str, Any], conn: ConnectionState):
        name = msg.get("username", "Unknown")
        peer_nonce = msg.get("nonce", "")
        auth = msg.get("auth", "")

        # 验证
        if self.crypto.network_secret and peer_nonce:
            if not self.crypto.verify_auth(name, peer_nonce, auth):
                print(f"[Control] {conn.ip} 握手认证失败，断开")
                await self._disconnect(conn.ip)
                return

        conn.username = name
        conn.peer_nonce = peer_nonce

        # 派生会话密钥
        if conn.local_nonce and conn.peer_nonce:
            conn.session_key = self.crypto.derive_session_key(
                conn.local_nonce, conn.peer_nonce
            )

        if not conn.handshake_done:
            conn.handshake_done = True
            print(f"[Control] 与 {conn.ip} 握手完成，用户: {name}")
            # 延迟 1 秒发布，确保 GUI 已订阅，避免事件时序问题
            asyncio.create_task(self._publish_peer_joined_later(conn.ip, name))

    async def _publish_peer_joined_later(self, ip: str, name: str):
        """延迟发布 PEER_JOINED，确保 GUI 已经订阅事件"""
        await asyncio.sleep(1.0)
        self.event_bus.publish(
            Events.PEER_JOINED,
            {"ip": ip, "name": name}
        )
        print(f"[Control] 已发布 PEER_JOINED: {name} ({ip})")

    # ========== 接收循环 ==========
    async def _receive_loop(self, ip: str):
        conn = self.connections.get(ip)
        if not conn:
            return
        try:
            while self.running and ip in self.connections:
                try:
                    data = await asyncio.wait_for(conn.reader.readline(), timeout=READ_TIMEOUT)
                except asyncio.TimeoutError:
                    print(f"[Control] {ip} 读取超时，断开")
                    break
                except (ConnectionResetError, ConnectionAbortedError, OSError):
                    break
                if not data:
                    break

                raw = data.decode("utf-8", errors="replace").strip()
                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict):
                        continue
                except json.JSONDecodeError:
                    continue

                # 解密
                if msg.get("type") == "secure":
                    if not conn.session_key:
                        print(f"[Control] {ip} 未握手，收到加密消息，忽略")
                        continue
                    unwrapped = self.crypto.unwrap(msg, conn.session_key)
                    if unwrapped is None:
                        print(f"[Control] {ip} 解密失败，断开")
                        break
                    msg = unwrapped

                msg_type = msg.get("type", "")

                # 传输层消息
                if msg_type == MsgType.HANDSHAKE:
                    await self._on_handshake(msg, conn)
                elif msg_type == MsgType.PING:
                    await self._send(ip, make_pong())
                elif msg_type == MsgType.PONG:
                    conn.last_pong = time.time()
                    # ===== 计算 RTT =====
                    if conn._ping_sent_at > 0:
                        conn.rtt_ms = (conn.last_pong - conn._ping_sent_at) * 1000.0
                        conn._ping_sent_at = 0.0
                        self.event_bus.publish(
                            "peer_latency",
                            {"ip": conn.ip, "rtt_ms": conn.rtt_ms}
                        )
                else:
                    # 业务消息：抛给上层
                    self.event_bus.publish(
                        "control_message",
                        {"ip": ip, "msg": msg}
                    )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[Control] {ip} 接收循环异常: {e}")
        finally:
            await self._disconnect(ip)

    # ========== 发送 ==========
    async def _raw_send(self, conn: ConnectionState, payload: Mapping[str, Any]):
        """发送不加密的消息（握手阶段用）"""
        try:
            async with conn.write_lock:
                conn.writer.write((json.dumps(payload) + "\n").encode("utf-8"))
                await conn.writer.drain()
        except Exception as e:
            print(f"[Control] 向 {conn.ip} 发送失败: {e}")

    async def _send(self, ip: str, payload: Mapping[str, Any]):
        conn = self.connections.get(ip)
        if not conn:
            return False
        # 加密（握手完成后）
        if conn.session_key:
            payload = self.crypto.wrap(payload, conn.session_key)
        try:
            async with conn.write_lock:
                conn.writer.write((json.dumps(payload) + "\n").encode("utf-8"))
                await conn.writer.drain()
            return True
        except Exception as e:
            print(f"[Control] 向 {ip} 发送失败: {e}")
            return False

    async def send_to(self, ip: str, payload: Mapping[str, Any]) -> bool:
        return await self._send(ip, payload)

    async def broadcast(self, payload: Mapping[str, Any], exclude_ip: Optional[str] = None):
        async with self._lock:
            ips = [ip for ip in self.connections.keys() if ip != exclude_ip]
        for ip in ips:
            await self._send(ip, payload)

    # ========== 心跳 ==========
    async def _heartbeat_loop(self):
        while self.running:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if not self.running:
                break
            now = time.time()
            to_disconnect = []
            async with self._lock:
                items = list(self.connections.items())
            for ip, conn in items:
                if now - conn.last_pong > HEARTBEAT_INTERVAL * 3:
                    to_disconnect.append(ip)
                elif conn.handshake_done:
                    try:
                        # ===== 记录发送时刻，用于计算 RTT =====
                        conn._ping_sent_at = time.time()
                        await self._send(ip, make_ping())
                    except Exception:
                        to_disconnect.append(ip)
            for ip in to_disconnect:
                print(f"[Control] {ip} 心跳超时，断开")
                await self._disconnect(ip)

    # ========== 断开 ==========
    async def _disconnect(self, ip: str):
        conn = None
        async with self._lock:
            conn = self.connections.pop(ip, None)
        if not conn:
            return
        # 关键：清理会话密钥（避免重连复用旧密钥）
        conn.session_key = None
        conn.handshake_done = False
        try:
            conn.writer.close()
            await conn.writer.wait_closed()
        except Exception:
            pass
        name = conn.username or ip
        print(f"[Control] 断开 {ip} ({name})")
        self.event_bus.publish(Events.PEER_LEFT, {"ip": ip, "name": name})

    async def send_to_raw(self, ip: str, payload: Mapping[str, Any]) -> bool:
        """发送不加密的消息（用于文件传输，因为 EasyTier 已经加密隧道）"""
        conn = self.connections.get(ip)
        if not conn:
            return False
        try:
            async with conn.write_lock:
                conn.writer.write((json.dumps(payload) + "\n").encode("utf-8"))
                await conn.writer.drain()
            return True
        except Exception as e:
            print(f"[Control] 向 {ip} 发送失败: {e}")
            return False

    # ========== 查询 ==========
    def is_connected(self, ip: str) -> bool:
        conn = self.connections.get(ip)
        return conn is not None and conn.handshake_done

    def get_latency(self, ip: str) -> float:
        """返回与对端的最近 RTT（毫秒），未测量返回 -1"""
        conn = self.connections.get(ip)
        if not conn:
            return -1.0
        return conn.rtt_ms