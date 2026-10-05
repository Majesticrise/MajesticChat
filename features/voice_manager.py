"""
语音会话管理
- 用户点击"加入语音" → 广播 VOICE_JOIN 到所有在线节点
- 所有节点把该用户 IP 加入 voice_channel.peers
- 采集线程 → Opus 编码 → UDP 广播
- UDP 收到 → 抖动缓冲 → 混音 → 播放
"""
import queue
import threading
import time
from typing import Any, Mapping

import numpy as np

from audio.capture import AudioCapture
from audio.codec import OpusCodec
from audio.jitter_buffer import JitterBuffer
from audio.mixer import Mixer
from audio.player import AudioPlayer
from core.config import FRAME_DURATION_MS
from core.event_bus import EventBus, Events
from core.protocol import MsgType
from network.control_channel import ControlChannel
from network.voice_channel import VoiceChannel


class VoiceManager:
    def __init__(self, username: str, self_ip: str,
                 control: ControlChannel, runtime, event_bus: EventBus):
        self.username = username
        self.self_ip = self_ip
        self.control = control
        self.runtime = runtime
        self.event_bus = event_bus

        # 用 IP 的哈希作为 sender_id（避免用户名冲突）
        self.self_id = hash(self_ip) & 0xFFFFFFFF
        self.voice_channel = VoiceChannel(self.self_id)
        self.voice_channel.set_recv_callback(self._on_voice_frame)

        # 音频组件
        self.frame_queue: queue.Queue = queue.Queue(maxsize=10)
        self.capture = AudioCapture(self.frame_queue)
        self.codec = OpusCodec()
        self.mixer = Mixer()
        self.player = AudioPlayer(self.mixer)

        # 每个发送者独立抖动缓冲
        self.jitter_buffers: dict[int, JitterBuffer] = {}

        # 状态
        self.in_voice = False
        self._send_task = None
        self._mix_task = None
        self.ptt_key = "ctrl+shift+v"

    async def start(self):
        self.event_bus.subscribe("control_message", self._on_control_message)
        self.event_bus.subscribe("user_toggle_voice", self._on_toggle_voice)
        self.event_bus.subscribe("user_set_volume", self._on_set_volume)
        self.event_bus.subscribe("user_set_voice_mode", self._on_set_mode)
        self.event_bus.subscribe("user_set_ptt_key", self._on_set_ptt_key)
        self.event_bus.subscribe("user_ptt_press", self._on_ptt_press)
        self.event_bus.subscribe("user_ptt_release", self._on_ptt_release)
        await self.voice_channel.start()

    async def stop(self):
        self.capture.stop()
        self.player.stop()
        await self.voice_channel.stop()

    # ========== 用户操作 ==========
    def _on_toggle_voice(self, data=None):
        self.runtime.submit(self._toggle_voice())

    async def _toggle_voice(self):
        if self.in_voice:
            await self._leave_voice()
        else:
            await self._join_voice()

    async def _join_voice(self):
        self.in_voice = True
        # 启动音频
        self.capture.start()
        self.player.start()

        # 通知所有在线节点
        await self.control.broadcast({
            "type": MsgType.VOICE_JOIN,
            "username": self.username,
            "ip": self.self_ip,
        })
        # 自己也加入 peers（其实不需要，因为不会给自己发）
        # 但需要把其他已在语音中的节点 IP 加入
        self.event_bus.publish(Events.VOICE_STATE, {"in_voice": True})
        print("[Voice] 已加入语音")

        # 启动发送任务
        self._send_task = self.runtime.submit(self._send_loop())
        self._mix_task = self.runtime.submit(self._mix_loop())

    async def _leave_voice(self):
        self.in_voice = False
        self.capture.stop()
        self.player.stop()

        await self.control.broadcast({
            "type": MsgType.VOICE_LEAVE,
            "username": self.username,
            "ip": self.self_ip,
        })
        self.voice_channel.clear_peers()
        self.jitter_buffers.clear()
        self.mixer.clear()
        self.event_bus.publish(Events.VOICE_STATE, {"in_voice": False})
        print("[Voice] 已退出语音")

    # ========== 发送循环 ==========
    async def _send_loop(self):
        """从采集队列取帧，编码后通过 UDP 发送"""
        import asyncio
        while self.in_voice:
            try:
                pcm_bytes = await asyncio.get_event_loop().run_in_executor(
                    None, self.frame_queue.get, True, 0.1
                )
            except queue.Empty:
                continue
            except Exception:
                break

            if not self.in_voice:
                break
            try:
                pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
                opus = self.codec.encode(pcm)
                self.voice_channel.send_frame(opus)
            except Exception as e:
                print(f"[Voice] 发送帧失败: {e}")

    # ========== 混音循环 ==========
    async def _mix_loop(self):
        """从混音器取混合帧，交给播放器"""
        import asyncio
        frame_interval = FRAME_DURATION_MS / 1000.0
        while self.in_voice:
            # 每个发送者的抖动缓冲出帧 → 混音器
            for sender_id, jb in list(self.jitter_buffers.items()):
                pcm_bytes = jb.pop()
                if pcm_bytes:
                    self.mixer.add(sender_id, pcm_bytes)

            # 混合并播放
            mixed = self.mixer.mix()
            self.player.push_play(mixed)

            await asyncio.sleep(frame_interval)

    # ========== 接收语音帧 ==========
    def _on_voice_frame(self, sender_id: int, seq: int, opus_data: bytes):
        """UDP 收到帧，解码后放入抖动缓冲"""
        try:
            # 获取或创建该发送者的抖动缓冲
            jb = self.jitter_buffers.get(sender_id)
            if jb is None:
                jb = JitterBuffer()
                self.jitter_buffers[sender_id] = jb

            # 解码
            pcm = self.codec.decode(opus_data)
            jb.push(seq, pcm.tobytes())
        except Exception:
            pass

    # ========== 网络消息 ==========
    def _on_control_message(self, data: Mapping[str, Any]):
        ip = data.get("ip")
        msg = data.get("msg", {})
        msg_type = msg.get("type", "")

        if msg_type == MsgType.VOICE_JOIN:
            peer_ip = msg.get("ip", "")
            if peer_ip and peer_ip != self.self_ip:
                self.voice_channel.add_peer(peer_ip)
                self.event_bus.publish(Events.VOICE_PEER_JOINED, {"ip": peer_ip})

            # 如果自己已经在语音，回复告知对方
            if self.in_voice:
                target_ip = ip if isinstance(ip, str) else ""
                self.runtime.submit(self._send_voice_state_to(target_ip))

        elif msg_type == MsgType.VOICE_LEAVE:
            peer_ip = msg.get("ip", "")
            self.voice_channel.remove_peer(peer_ip)
            self.event_bus.publish(Events.VOICE_PEER_LEFT, {"ip": peer_ip})

    async def _send_voice_state_to(self, ip: str):
        await self.control.send_to(ip, {
            "type": MsgType.VOICE_JOIN,
            "username": self.username,
            "ip": self.self_ip,
        })

    def _on_set_volume(self, data: Mapping[str, Any] | None):
        if data is None:
            return
        volume = data.get("volume", 80.0)
        try:
            self.capture.set_volume(float(volume))
        except Exception:
            pass

    def _on_set_mode(self, data: Mapping[str, Any] | None):
        if data is None:
            return
        mode = str(data.get("mode", "vad")).strip().lower()
        if mode not in {"vad", "ptt"}:
            return
        self.capture.set_mode(mode)

    def _on_set_ptt_key(self, data: Mapping[str, Any] | None):
        if data is None:
            return
        key = str(data.get("key", self.ptt_key)).strip()
        if not key:
            return
        self.ptt_key = key.lower()

    def _on_ptt_press(self, data: Mapping[str, Any] | None):
        self.capture.set_ptt(True)

    def _on_ptt_release(self, data: Mapping[str, Any] | None):
        self.capture.set_ptt(False)

    def set_ptt_key(self, key: str):
        self._on_set_ptt_key({"key": key})