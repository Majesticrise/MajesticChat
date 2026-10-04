"""
抖动缓冲：平滑网络抖动，保证播放连续。
- 使用固定长度的 deque，按序列号排序
- 动态调整缓冲大小（40~100ms）
"""
import time
from collections import deque
from typing import Optional


class JitterBuffer:
    def __init__(self, target_ms: int = 60, frame_ms: int = 20):
        self.target_ms = target_ms
        self.frame_ms = frame_ms
        self.max_frames = 20       # 最多缓存 400ms
        self.buffer: deque = deque(maxlen=self.max_frames)
        self.last_seq = -1
        self.last_pop_time = 0.0

    def push(self, seq: int, pcm_bytes: bytes):
        # 丢弃过时的包
        if self.last_seq >= 0 and seq <= self.last_seq:
            return
        self.buffer.append((seq, pcm_bytes, time.time()))

    def pop(self) -> Optional[bytes]:
        """取出一帧用于播放；没有就返回 None"""
        now = time.time()
        # 缓冲满或达到目标延时后开始出帧
        if not self.buffer:
            return None

        # 计算缓冲延时
        if len(self.buffer) >= 2:
            oldest_time = self.buffer[0][2]
            buffer_delay_ms = (now - oldest_time) * 1000
        else:
            buffer_delay_ms = 0

        # 未达到目标延时，先等
        if buffer_delay_ms < self.target_ms and len(self.buffer) < self.max_frames:
            # 但如果是第一个包，允许直接出（避免开头延迟）
            if self.last_seq < 0:
                pass
            else:
                return None

        seq, pcm, _ = self.buffer.popleft()
        self.last_seq = seq
        return pcm

    def clear(self):
        self.buffer.clear()
        self.last_seq = -1