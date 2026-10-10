# coding=utf-8
"""TTS 文本转语音, 最终产出 G.711 裸音频文件(16kHz, 供对讲下发到摄像头扬声器)"""
import hashlib
import json
import os
import subprocess
import urllib.request

try:
    import edge_tts
except ImportError:
    edge_tts = None

# 摄像头扬声器按 16kHz 消费音频(实测: 喂 8kHz 会被当 16kHz 播 -> 只有一半数据量 -> 卡顿),
# 所以 TTS 直接产出 16kHz G.711, 并如实声明采样率。改这里必须同步 camera.py 的 speak_file。
TTS_RATE = 16000


def _ffmpeg_to_raw_codec(mp3_path, sample_rate, codec):
    """mp3 -> 指定裸编码(alaw/mulaw) mono raw, 返回字节"""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", mp3_path,
        "-ar", str(sample_rate), "-ac", "1",
        "-f", codec, "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 转换失败: {proc.stderr.decode(errors='ignore')}")
    return proc.stdout


def _synth_edge(text, voice, out_path):
    if edge_tts is None:
        raise RuntimeError("未安装 edge-tts")
    import asyncio

    asyncio.run(edge_tts.Communicate(text, voice).save(out_path))


def _synth_openai(text, cfg, out_path):
    """调用 OpenAI 兼容 /v1/audio/speech"""
    url = cfg["ai"].get("base_url", "").rstrip("/") + "/audio/speech"
    payload = {
        "model": cfg["ai"].get("tts_model", "tts-1"),
        "input": text,
        "voice": cfg["ai"].get("tts_voice", "alloy"),
    }
    data = urllib.request.urlopen(
        urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + cfg["ai"].get("api_key", ""),
            },
        ),
        timeout=60,
    ).read()
    with open(out_path, "wb") as f:
        f.write(data)


def text_to_g711a(text, cfg, out_path, codec="mulaw"):
    """文本 -> 裸编码音频文件(alaw/mulaw). 返回 (文件路径, 时长秒).

    带缓存: 相同文本+codec 的音频直接复用, 避免重复 TTS(尤其 voice 常用固定回复).
    """
    cache_dir = os.path.join(os.path.dirname(out_path), ".tts_cache")
    os.makedirs(cache_dir, exist_ok=True)

    voice = cfg["tts"].get("edge_voice", "zh-CN-XiaoxiaoNeural")
    backend = cfg["tts"].get("backend", "edge")
    cache_key = hashlib.md5(
        f"{backend}|{voice}|{codec}|{TTS_RATE}|{text}".encode()
    ).hexdigest()[:16]
    cache_file = os.path.join(cache_dir, f"{cache_key}.{codec}")

    # 命中缓存直接返回
    if os.path.exists(cache_file):
        duration = os.path.getsize(cache_file) / TTS_RATE
        return cache_file, duration

    tmp_mp3 = out_path + ".mp3"

    try:
        if backend == "edge":
            _synth_edge(text, voice, tmp_mp3)
        elif backend == "openai":
            _synth_openai(text, cfg, tmp_mp3)
        else:
            raise RuntimeError(f"未知 TTS backend: {backend}")

        raw = _ffmpeg_to_raw_codec(tmp_mp3, TTS_RATE, codec)
        with open(cache_file, "wb") as f:
            f.write(raw)
        duration = len(raw) / TTS_RATE
        return cache_file, duration
    finally:
        if os.path.exists(tmp_mp3):
            os.remove(tmp_mp3)


def beep(out_path, freq=880, seconds=0.3, codec="mulaw", rate=TTS_RATE):
    """生成一段提示音(G.711 裸音频), 用于测试摄像头扬声器"""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate={rate}",
        "-ac", "1", "-ar", str(rate), "-f", codec, out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"生成提示音失败: {proc.stderr.decode(errors='ignore')}")
    return out_path, seconds
