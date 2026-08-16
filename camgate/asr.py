# coding=utf-8
"""ASR 语音识别: 把 PCM 音频转文字. 基于 faster-whisper(本地离线, CPU 可用)."""
import os
import tempfile

# 模型已在本地缓存, 加载时禁止联网检查(HF 会在加载时尝试访问 Hub, 无代理时卡住)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

_WHISPER_MODEL = os.environ.get("ASR_MODEL", "small")
_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        # CPU int8 加速, 指定计算设备
        _model = WhisperModel(_WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def transcribe_pcm16(pcm16: bytes, sample_rate: int = 8000, language: str = "zh") -> str:
    """16bit PCM mono -> 文字. 返回识别文本(失败时返回空串)."""
    if not pcm16:
        return ""
    # 写成临时 wav 再交给 faster-whisper
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        _write_wav(path, pcm16, sample_rate)
        model = _get_model()
        segments, _info = model.transcribe(
            path,
            language=language,
            beam_size=1,  # 短指令 beam=1 足够, 比 5 快数倍
            vad_filter=True,
            vad_parameters={
                "threshold": 0.6,  # VAD 敏感度, 默认0.5; 设备底噪大, 略调高滤噪音但不过严
                "min_silence_duration_ms": 500,
            },
        )
        return "".join(seg.text for seg in segments).strip()
    except Exception:
        return ""
    finally:
        if os.path.exists(path):
            os.remove(path)


def _write_wav(path, pcm16: bytes, sample_rate: int):
    """写 16bit PCM 单声道 wav"""
    import struct
    import wave

    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16)