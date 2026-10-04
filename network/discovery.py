"""
节点发现
每 DISCOVERY_INTERVAL 秒轮询 EasyTier peer 列表，
发现新节点时发布 PEER_JOINED 事件，节点离线时发布 PEER_LEFT。
"""
import asyncio

from core.event_bus import EventBus, Events
from network.easytier_manager import EasyTierManager
from network.peer_manager import PeerManager

DISCOVERY_INTERVAL = 15   # 秒


class Discovery:
    def __init__(self,
                 easytier: EasyTierManager,
                 peer_manager: PeerManager,
                 event_bus: EventBus):
        self.easytier = easytier
        self.peer_manager = peer_manager
        self.event_bus = event_bus
        self.self_ip: str | None = None
        self._running = False

    async def start(self):
        self._running = True
        self.self_ip = self.easytier.get_self_ip()
        print(f"[Discovery] 本机虚拟 IP: {self.self_ip}")
        # 立即执行一次，然后进入循环
        try:
            await self._refresh_once()
        except Exception as e:
            print(f"[Discovery] 首次刷新异常: {e}")
        await self._refresh_loop()

    async def _refresh_loop(self):
        while self._running:
            await asyncio.sleep(DISCOVERY_INTERVAL)
            if not self._running:
                break
            try:
                await self._refresh_once()
            except Exception as e:
                print(f"[Discovery] 刷新异常: {e}")

    async def _refresh_once(self):
        current_ips = self.easytier.get_peer_ips()
        current_ips.discard(self.self_ip)
        known = self.peer_manager.all_ips()

        # 新节点
        for ip in current_ips - known:
            self.peer_manager.add_or_update(ip)
            peer = self.peer_manager.get(ip)
            print(f"[Discovery] 发现新节点: {ip}")
            self.event_bus.publish(
                Events.PEER_JOINED,
                {"ip": ip, "name": peer.name or ip}
            )

        # 离线节点
        for ip in known - current_ips:
            peer = self.peer_manager.get(ip)
            name = peer.name if peer else ip
            self.peer_manager.remove(ip)
            print(f"[Discovery] 节点离线: {ip}")
            self.event_bus.publish(
                Events.PEER_LEFT,
                {"ip": ip, "name": name}
            )

    def stop(self):
        self._running = False