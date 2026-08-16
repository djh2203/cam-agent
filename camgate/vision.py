# coding=utf-8
"""本地视觉理解: 用 Qwen3.5-0.8B 分析摄像头画面.

通过本地 llama-server (llama.cpp 官方二进制, OpenAI 兼容 API) 提供视觉能力:
  llama-server -m Qwen3.5-0.8B-Q4_K_M.gguf --mmproj mmproj-BF16.gguf \\
      --host 127.0.0.1 --port 8081 --reasoning off

llama-cpp-python 的 mmproj 参数在 0.3.34 中不生效(视觉从未真正加载), 故改用 llama-server。
模型文件在 models/qwen35-0.8b-unsloth/, 适合 4G 内存无 GPU 服务器。
"""
import base64
import json
import os
import urllib.request

_DEFAULT_URL = os.environ.get("VISION_BASE_URL", "http://127.0.0.1:8081")


def analyze_image(image_path: str, prompt: str = "这张图片里有什么?用中文一句话描述。") -> str:
    """分析一张本地图片, 返回文字描述.

    Args:
        image_path: 本地图片路径 (jpg/png)。
        prompt: 提问, 默认中文描述图片内容。
    Returns:
        模型输出的文字。
    Raises:
        FileNotFoundError: 图片不存在。
        RuntimeError: llama-server 未启动或调用失败。
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"图片不存在: {image_path}")

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    url = _DEFAULT_URL.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": "qwen",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": 300,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError:
        # 服务未启动, 尝试自动拉起
        from .vision_server import ensure_running, health

        if not health():
            ensure_running()
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode())
    return (data["choices"][0]["message"].get("content") or "").strip()