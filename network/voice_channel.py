"""
UDP 语音通道
- 使用独立 UDP socket 发送/接收音频帧
- 帧格式: [sender_id(4B)][seq(4B)][timestamp(8B)][opus_data]
- 不加密（降低延迟）
"""
import asyncio
import socket
import struct
import time
from typing import Callable, Dict, Set

from core.config import VOICE_PORT


HEADER_FORMAT = "!IIQ"    # sender_id, seq, timestamp
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


class VoiceChannel:
    def __init__(self, self_id: int):
        self.self_id = self_id
        self.sock: socket.socket | None = None
        self._running = False
        self._recv_callback: Callable[[int, int, bytes], None] | None = None
        # 语音会话参与者 IP 集合
        self.peers: Set[str] = set()
        self._send_seq = 0

    def set_recv_callback(self, callback: Callable[[int, int, bytes], None]):
        """callback(sender_id, seq, opus_data)"""
        self._recv_callback = callback

    async def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", VOICE_PORT))
        self.sock.setblocking(False)
        self._running = True
        asyncio.create_task(self._recv_loop())
        print(f"[Voice] UDP 监听端口 {VOICE_PORT}")

    async def stop(self):
        self._running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    async def _recv_loop(self):
        loop = asyncio.get_event_loop()
        while self._running:
            sock = self.sock
            if sock is None:
                break
            try:
                data, addr = await loop.sock_recvfrom(sock, 4096)
            except Exception:
                if not self._running:
                    break
                continue

            if len(data) < HEADER_SIZE:
                continue

            try:
                sender_id, seq, ts = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
                opus_data = data[HEADER_SIZE:]
                if self._recv_callback:
                    self._recv_callback(sender_id, seq, opus_data)
            except Exception:
                continue

    def send_frame(self, opus_data: bytes):
        """向所有语音会话参与者广播一帧"""
        if not self.sock or not self.peers:
            return
        self._send_seq += 1
        header = struct.pack(HEADER_FORMAT, self.self_id, self._send_seq, int(time.time() * 1000))
        packet = header + opus_data
        for ip in self.peers:
            try:
                self.sock.sendto(packet, (ip, VOICE_PORT))
            except Exception:
                pass

    def add_peer(self, ip: str):
        self.peers.add(ip)

    def remove_peer(self, ip: str):
        self.peers.discard(ip)

    def clear_peers(self):
        self.peers.clear()