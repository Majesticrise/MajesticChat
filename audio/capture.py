"""
麦克风采集：独立线程，通过 sounddevice 回调把每帧音频塞进队列。
支持 VAD（自由发言）和 PTT（按键说话）两种模式。
"""
import queue
import threading
import time

import numpy as np
import sounddevice as sd

from core.config import SAMPLE_RATE, CHANNELS, FRAME_SIZE, FRAME_DURATION_MS, VAD_ENERGY_THRESHOLD, VAD_SILENCE_FRAMES


class AudioCapture:
    def __init__(self, frame_queue: queue.Queue):
        self.frame_queue = frame_queue
        self._stream = None
        self._running = False

        # 模式
        self.mode = "vad"           # "vad" 或 "ptt"
        self.ptt_active = False
        self.mic_volume = 1.0       # 0.0 ~ 1.0

        # VAD 状态
        self._silence_count = 0

    def start(self):
        if self._running:
            return
        self._running = True
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=FRAME_SIZE,
            callback=self._callback,
        )
        self._stream.start()
        print("[Audio] 麦克风已启动")

    def stop(self):
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        print("[Audio] 麦克风已停止")

    def set_mode(self, mode: str):
        self.mode = mode

    def set_ptt(self, active: bool):
        self.ptt_active = active

    def set_volume(self, volume_0_100: float):
        self.mic_volume = max(0.0, min(1.0, volume_0_100 / 100.0))

    def _callback(self, indata, frames, time_info, status):
        if not self._running:
            return
        if status:
            # 忽略溢出等状态
            pass

        # 复制并应用音量
        pcm = indata[:, 0].copy()
        if self.mic_volume != 1.0:
            pcm = (pcm * self.mic_volume).astype(np.int16)

        # 判断是否要发送
        should_send = False
        if self.mode == "ptt":
            should_send = self.ptt_active
        else:  # vad
            energy = self._calc_energy(pcm)
            if energy > VAD_ENERGY_THRESHOLD:
                should_send = True
                self._silence_count = 0
            else:
                self._silence_count += 1
                # 静音超过阈值，停止发送
                should_send = self._silence_count < VAD_SILENCE_FRAMES

        if should_send:
            try:
                self.frame_queue.put_nowait(pcm.tobytes())
            except queue.Full:
                pass

    @staticmethod
    def _calc_energy(pcm: np.ndarray) -> float:
        """计算短时能量（RMS × 1000）"""
        if len(pcm) == 0:
            return 0.0
        samples = pcm.astype(np.float32)
        rms = np.sqrt(np.mean(samples * samples))
        return rms