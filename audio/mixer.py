"""
多人混音：将多个发送者的 PCM 帧叠加，clip 到 int16 范围。
"""
from typing import Dict
import numpy as np

from core.config import FRAME_SIZE


class Mixer:
    def __init__(self):
        # sender_id -> 最近的 PCM
        self.frames: Dict[int, np.ndarray] = {}
        # sender_id -> 增益倍数（0.0 ~ 2.0）
        self.gains: Dict[int, float] = {}

    def set_gain(self, sender_id: int, gain: float):
        try:
            gain_value = float(gain)
        except Exception:
            return
        if gain_value < 0:
            gain_value = 0.0
        self.gains[sender_id] = max(0.0, min(3.0, gain_value))

    def add(self, sender_id: int, pcm_bytes: bytes):
        try:
            pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
            if len(pcm) != FRAME_SIZE:
                return
            self.frames[sender_id] = pcm
        except Exception:
            pass

    def remove(self, sender_id: int):
        self.frames.pop(sender_id, None)
        self.gains.pop(sender_id, None)

    def mix(self) -> bytes:
        """混合当前所有帧，返回 int16 bytes"""
        if not self.frames:
            return b"\x00" * (FRAME_SIZE * 2)

        acc = np.zeros(FRAME_SIZE, dtype=np.float64)
        for sender_id, pcm in self.frames.items():
            gain = self.gains.get(sender_id, 1.0)
            acc += pcm.astype(np.float64) * gain

        acc = np.clip(acc, -32768, 32767).astype(np.int16)
        return acc.tobytes()

    def clear(self):
        self.frames.clear()
        self.gains.clear()