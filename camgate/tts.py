# coding=utf-8
"""TTS 文本转语音, 最终产出 G711A 裸音频文件(供对讲下发到摄像头扬声器)"""
import os
import subprocess
import tempfile
import time
import urllib.request

try:
    import edge_tts
except ImportError:
    edge_tts = None

A_LAW_LOOKUP = None


def _build_alaw_table():
    """G.711 A-law 编码表 (16bit PCM -> 8bit A-law)"""
    global A_LAW_LOOKUP
    if A_LAW_LOOKUP is not None:
        return A_LAW_LOOKUP
    table = []
    for i in range(-32768, 32768):
        pcm = i
        sign = (pcm >> 8) & 0x80
        if sign:
            pcm = -pcm
        if pcm > 32635:
            pcm = 32635
        pcm = (pcm << 2) + 8
        if pcm & 0x4000:
            pcm ^= 0x4000
        seg = 0
        while (pcm & 0x2000) == 0 and seg < 7:
            pcm <<= 1
            seg += 1
        alaw = (seg << 4) | ((pcm >> 8) & 0x0F)
        alaw ^= 0x55 if sign else 0xD5
        table.append(alaw & 0xFF)
    A_LAW_LOOKUP = table
    return table


def pcm16_to_g711a(pcm16: bytes) -> bytes:
    """16bit PCM mono -> G711A bytes (每样本 1 字节)"""
    table = _build_alaw_table()
    import struct

    count = len(pcm16) // 2
    samples = struct.unpack("<%dh" % count, pcm16[: count * 2])
    return bytes(table[s + 32768] for s in samples)


def _ffmpeg_to_pcm(mp3_path, sample_rate=8000):
    """mp3 -> 16bit PCM mono raw, 返回字节"""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", mp3_path,
        "-ar", str(sample_rate), "-ac", "1",
        "-f", "s16le", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 转换失败: {proc.stderr.decode(errors='ignore')}")
    return proc.stdout


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
    import threading

    async def _run():
        comm = edge_tts.Communicate(text, voice)
        await comm.save(out_path)

    result = {}

    def _worker():
        try:
            asyncio.run(_run())
            result["ok"] = True
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    if "error" in result:
        raise result["error"]


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
            data=json_dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + cfg["ai"].get("api_key", ""),
            },
        ),
        timeout=60,
    ).read()
    with open(out_path, "wb") as f:
        f.write(data)


def json_dumps(d):
    import json

    return json.dumps(d)


def text_to_g711a(text, cfg, out_path, codec="mulaw"):
    """文本 -> 裸编码音频文件(alaw/mulaw). 返回 (文件路径, 时长秒)"""
    backend = cfg["tts"].get("backend", "edge")
    tmp_mp3 = out_path + ".mp3"

    try:
        if backend == "edge":
            _synth_edge(text, cfg["tts"].get("edge_voice", "zh-CN-XiaoxiaoNeural"), tmp_mp3)
        elif backend == "openai":
            _synth_openai(text, cfg, tmp_mp3)
        else:
            raise RuntimeError(f"未知 TTS backend: {backend}")

        raw = _ffmpeg_to_raw_codec(tmp_mp3, 8000, codec)
        with open(out_path, "wb") as f:
            f.write(raw)
        duration = len(raw) / 8000.0
        return out_path, duration
    finally:
        if os.path.exists(tmp_mp3):
            os.remove(tmp_mp3)


def beep_g711a(out_path, freq=880, seconds=0.3):
    """生成一段提示音(G711A), 用于测试扬声器"""
    import math

    pcm = bytearray()
    for i in range(int(8000 * seconds)):
        val = int(3000 * math.sin(2 * math.pi * freq * i / 8000))
        pcm += int(val).to_bytes(2, "little", signed=True)
    g711a = pcm16_to_g711a(bytes(pcm))
    with open(out_path, "wb") as f:
        f.write(g711a)
    return out_path, seconds