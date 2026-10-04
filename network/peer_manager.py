"""
对等节点状态管理
记录节点 IP、名称、连接状态、首次/最后出现时间
"""
import time
from typing import Optional


class Peer:
    def __init__(self, ip: str, name: str = ""):
        self.ip = ip
        self.name = name
        self.first_seen = time.time()
        self.last_seen = time.time()
        self.connected = False         # 阶段 3 使用
        self.connection_type = "Unknown"  # P2P / Relay，阶段 3 使用

    def touch(self):
        self.last_seen = time.time()


class PeerManager:
    def __init__(self):
        self.peers: dict[str, Peer] = {}

    def add_or_update(self, ip: str) -> bool:
        """返回 True 表示这是一个新节点"""
        if ip in self.peers:
            self.peers[ip].touch()
            return False
        self.peers[ip] = Peer(ip)
        return True

    def remove(self, ip: str):
        self.peers.pop(ip, None)

    def get(self, ip: str) -> Optional[Peer]:
        return self.peers.get(ip)

    def set_name(self, ip: str, name: str):
        if ip in self.peers:
            self.peers[ip].name = name

    def all_ips(self) -> set[str]:
        return set(self.peers.keys())

    def list_peers(self) -> list[Peer]:
        return list(self.peers.values())

    def clear(self):
        self.peers.clear()