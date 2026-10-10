# coding=utf-8
import configparser
import os
import sys
from pathlib import Path

DEFAULT_CONFIG = {
    # 摄像头凭据不在这里(见 mcp-dahua-camera 的 config.ini / cameras.json)
    "server": {"host": "127.0.0.1", "port": "8080"},
    "ai": {
        # 仅 [tts] backend = openai 时使用
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "tts_model": "tts-1",
        "tts_voice": "alloy",
    },
    "tts": {
        "backend": "edge",
        "edge_voice": "zh-CN-XiaoxiaoNeural",
        "language": "zh-CN",
    },
}


def _env(name, default):
    return os.environ.get(name, default)


def load_config(path=None):
    """加载 config.ini,环境变量可覆盖: GATE_HOST/GATE_PORT, AI_*, TTS_*"""
    cfg = configparser.ConfigParser()
    cfg.read_dict(DEFAULT_CONFIG)

    if path is None:
        # 依次查找: 当前目录 / 仓库根目录
        candidates = [
            Path("config.ini"),
            Path(__file__).parent.parent / "config.ini",
        ]
    else:
        candidates = [Path(path)]

    for cand in candidates:
        if cand.exists():
            cfg.read(cand)
            print(f"加载配置: {cand}", file=sys.stderr)
            break
    else:
        print("未找到 config.ini,使用默认值(可复制 config.example.ini)", file=sys.stderr)

    overrides = {
        "server": {"host": ("GATE_HOST", "host"), "port": ("GATE_PORT", "port")},
        "ai": {
            "base_url": ("AI_BASE_URL", "base_url"),
            "api_key": ("AI_API_KEY", "api_key"),
            "tts_model": ("AI_TTS_MODEL", "tts_model"),
            "tts_voice": ("AI_TTS_VOICE", "tts_voice"),
        },
        "tts": {
            "backend": ("TTS_BACKEND", "backend"),
            "edge_voice": ("TTS_EDGE_VOICE", "edge_voice"),
            "language": ("TTS_LANGUAGE", "language"),
        },
    }
    for section, fields in overrides.items():
        for field, (envname, key) in fields.items():
            val = _env(envname, None)
            if val is not None:
                cfg.set(section, key, val)

    return cfg