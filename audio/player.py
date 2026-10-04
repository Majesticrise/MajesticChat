"""
扬声器播放：独立线程，从抖动缓冲 + 混音器取数据，写入 sounddevice。
"""
import queue
import threading
import time

import numpy as np
import sounddevice as sd

from core.config import SAMPLE_RATE, CHANNELS, FRAME_SIZE
from audio.mixer import Mixer


class AudioPlayer:
    def __init__(self, mixer: Mixer):
        self.mixer = mixer
        self._stream = None
        self._running = False
        self._output_queue: queue.Queue = queue.Queue(maxsize=10)

    def start(self):
        if self._running:
            return
        self._running = True
        self._stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=FRAME_SIZE,
            callback=self._callback,
        )
        self._stream.start()
        print("[Audio] 扬声器已启动")

    def stop(self):
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        print("[Audio] 扬声器已停止")

    def push_play(self, pcm_bytes: bytes):
        """把要播放的 PCM 帧放入输出队列"""
        try:
            self._output_queue.put_nowait(pcm_bytes)
        except queue.Full:
            # 队列满，丢弃旧帧（降低延迟优先）
            try:
                self._output_queue.get_nowait()
                self._output_queue.put_nowait(pcm_bytes)
            except Exception:
                pass

    def _callback(self, outdata, frames, time_info, status):
        if not self._running:
            outdata[:] = 0
            return
        try:
            pcm_bytes = self._output_queue.get_nowait()
        except queue.Empty:
            # 无数据，填充静音
            outdata[:] = 0
            return

        try:
            pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
            if len(pcm) < frames:
                # 不足一帧，补零
                padded = np.zeros(frames, dtype=np.int16)
                padded[:len(pcm)] = pcm
                outdata[:, 0] = padded
            else:
                outdata[:, 0] = pcm[:frames]
        except Exception:
            outdata[:] = 0