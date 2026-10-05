"""
Opus 编解码（兼容 pyogg 多个版本的 API）
"""
import numpy as np

from core.config import SAMPLE_RATE, CHANNELS, OPUS_BITRATE


class OpusCodec:
    def __init__(self):
        import pyogg

        # 优先使用 Buffered 版本（新 API），回退到普通版本
        EncoderClass = (getattr(pyogg, "OpusBufferedEncoder", None)
                        or getattr(pyogg, "OpusEncoder", None))
        DecoderClass = (getattr(pyogg, "OpusBufferedDecoder", None)
                        or getattr(pyogg, "OpusDecoder", None))

        if EncoderClass is None or DecoderClass is None:
            raise ImportError("pyogg 中未找到 Opus 编解码器类")

        self._encoder = self._create_encoder(EncoderClass)
        self._decoder = self._create_decoder(DecoderClass)

    # ========== 编码器 ==========
    @staticmethod
    def _create_encoder(encoder_cls):
        # 尝试 1：用构造参数
        try:
            return encoder_cls(
                channels=CHANNELS,
                sample_rate=SAMPLE_RATE,
                max_bitrate=OPUS_BITRATE,
            )
        except Exception:
            pass

        # 尝试 2：无参构造 + 逐个 set
        try:
            enc = encoder_cls()
        except Exception as e:
            raise RuntimeError(f"无法构造 Opus 编码器: {e}")

        OpusCodec._try_call(enc, ["set_application"], "audio")
        OpusCodec._try_call(enc, ["set_sampling_frequency", "set_sample_rate"], SAMPLE_RATE)
        OpusCodec._try_call(enc, ["set_channels"], CHANNELS)
        OpusCodec._try_call(
            enc,
            ["set_bitrate", "set_bit_rate", "set_max_bitrate", "set_bit_rate_bps"],
            OPUS_BITRATE,
        )
        return enc

    # ========== 解码器 ==========
    @staticmethod
    def _create_decoder(decoder_cls):
        try:
            return decoder_cls(
                channels=CHANNELS,
                sample_rate=SAMPLE_RATE,
            )
        except Exception:
            pass

        try:
            dec = decoder_cls()
        except Exception as e:
            raise RuntimeError(f"无法构造 Opus 解码器: {e}")

        OpusCodec._try_call(dec, ["set_channels"], CHANNELS)
        OpusCodec._try_call(dec, ["set_sampling_frequency", "set_sample_rate"], SAMPLE_RATE)
        return dec

    # ========== 工具：尝试多个方法名 ==========
    @staticmethod
    def _try_call(obj, method_names, value):
        for name in method_names:
            m = getattr(obj, name, None)
            if callable(m):
                try:
                    m(value)
                    return True
                except Exception:
                    continue
        return False

    # ========== 编码 ==========
    def encode(self, pcm: np.ndarray) -> bytes:
        raw = pcm.astype(np.int16).tobytes()
        try:
            encoded = self._encoder.encode(raw)
        except TypeError:
            # 某些版本 encode 需要额外的 frame_size 参数
            encoded = self._encoder.encode(raw, len(pcm))
        return bytes(encoded)

    # ========== 解码 ==========
    def decode(self, opus_data: bytes) -> np.ndarray:
        try:
            pcm_bytes = self._decoder.decode(bytearray(opus_data))
        except TypeError:
            # 有些版本的 decode 需要额外的参数
            pcm_bytes = self._decoder.decode(bytearray(opus_data), 0, 0)
        return np.frombuffer(bytes(pcm_bytes), dtype=np.int16)