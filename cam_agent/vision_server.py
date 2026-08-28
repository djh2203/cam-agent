# coding=utf-8
"""本地视觉服务(llama-server)管理: 启动/停止/健康检查.

用 llama.cpp 官方预编译二进制 llama-server 加载 Qwen3.5-0.8B + mmproj,
提供 OpenAI 兼容 API (默认 http://127.0.0.1:8081), 供 vision.analyze_image 调用.
"""
import os
import subprocess
import sys
import time
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODEL_DIR = os.path.join(_ROOT, "models", "qwen35-0.8b-unsloth")
_TEXT_GGUF = os.path.join(_MODEL_DIR, "Qwen3.5-0.8B-Q4_K_M.gguf")
_MMPROJ = os.path.join(_MODEL_DIR, "mmproj-BF16.gguf")
_SERVER_BIN = os.path.join(_ROOT, "vendor", "llama", "llama-b9543", "llama-server")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8081


def _base_url(port=DEFAULT_PORT):
    return f"http://127.0.0.1:{port}"


def _find_server():
    """查找 llama-server 二进制. 优先 vendor 内, 其次系统 PATH."""
    if os.path.exists(_SERVER_BIN):
        return _SERVER_BIN
    from shutil import which

    return which("llama-server")


def health(port=DEFAULT_PORT, timeout=3):
    """检查本地视觉服务是否在线."""
    try:
        with urllib.request.urlopen(_base_url(port) + "/health", timeout=timeout) as r:
            return json_load(r.read()) == {"status": "ok"}
    except Exception:
        return False


def json_load(raw):
    import json

    return json.loads(raw.decode())


def ensure_running(port=DEFAULT_PORT, wait=30):
    """确保 llama-server 在运行. 未运行则启动之. 返回 True 表示就绪."""
    if health(port):
        return True
    server = _find_server()
    if not server:
        raise FileNotFoundError(
            "未找到 llama-server。请下载 llama.cpp 预编译包放到 vendor/llama/, "
            "或安装 llama-server 到 PATH。"
        )
    if not (os.path.exists(_TEXT_GGUF) and os.path.exists(_MMPROJ)):
        raise FileNotFoundError(
            f"缺少本地视觉模型: {_TEXT_GGUF} / {_MMPROJ}\n"
            "请先下载: unsloth/Qwen3.5-0.8B-GGUF 的 Q4_K_M.gguf 和 mmproj-BF16.gguf 到 models/qwen35-0.8b-unsloth/"
        )
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = os.path.dirname(server) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    cmd = [
        server,
        "-m", _TEXT_GGUF,
        "--mmproj", _MMPROJ,
        "--host", DEFAULT_HOST,
        "--port", str(port),
        "-c", "4096",
        "-t", "8",
        "--no-warmup",
        "--reasoning", "off",
    ]
    subprocess.Popen(
        cmd,
        env=env,
        stdout=open(os.path.join(_ROOT, "media", "llama_server.log"), "ab"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + wait
    while time.time() < deadline:
        if health(port):
            return True
        time.sleep(1)
    raise RuntimeError(f"llama-server 启动失败, 请查看 media/llama_server.log")


def stop(port=DEFAULT_PORT):
    """停止占用指定端口的 llama-server."""
    import signal

    try:
        with urllib.request.urlopen(_base_url(port) + "/props", timeout=3) as r:
            import json

            props = json.loads(r.read().decode())
        pid = props.get("total_slots")
        return
    except Exception:
        pass
    os.system(f"fuser -k {port}/tcp 2>/dev/null || true")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    if cmd == "start":
        print(f"启动本地视觉服务 llama-server (端口 {port})...")
        if ensure_running(port):
            print(f"视觉服务就绪: {_base_url(port)}")
    elif cmd == "stop":
        stop(port)
        print("已请求停止")
    elif cmd == "status":
        print("在线" if health(port) else "离线")
    else:
        print("用法: python -m camgate vision-server {start|stop|status} [端口]")


if __name__ == "__main__":
    main()