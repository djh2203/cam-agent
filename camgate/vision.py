# coding=utf-8
"""本地视觉理解: 用 Qwen3.5-0.8B (GGUF + mmproj, llama-cpp-python) 分析摄像头画面。

- 模型文件默认放在 models/qwen35-0.8b-unsloth/ 下(文本 GGUF + 配套视觉投影)。
- 懒加载单例, 首次调用时加载 ~700MB, 之后复用。
- 适合 4G 内存的无 GPU 服务器 (Q4_K_M 文本 ~508MB + mmproj ~198MB)。
"""
import os

_MODEL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models",
    "qwen35-0.8b-unsloth",
)
_TEXT_GGUF = os.path.join(_MODEL_DIR, "Qwen3.5-0.8B-Q4_K_M.gguf")
_MMPROJ = os.path.join(_MODEL_DIR, "mmproj-BF16.gguf")

_llm = None


def _load():
    global _llm
    if _llm is None:
        from llama_cpp import Llama

        text_gguf = os.environ.get("VISION_TEXT_GGUF", _TEXT_GGUF)
        mmproj = os.environ.get("VISION_MMPROJ", _MMPROJ)
        if not (os.path.exists(text_gguf) and os.path.exists(mmproj)):
            raise FileNotFoundError(
                f"缺少本地视觉模型: {text_gguf} / {mmproj} "
                "(可用环境变量 VISION_TEXT_GGUF / VISION_MMPROJ 指定)"
            )
        _llm = Llama(
            model_path=text_gguf,
            mmproj=mmproj,
            n_ctx=2048,
            verbose=False,
        )
    return _llm


def analyze_image(image_path: str, prompt: str = "这张图片里有什么?用中文一句话描述。") -> str:
    """分析一张本地图片, 返回文字描述。

    Args:
        image_path: 本地图片路径 (jpg/png)。
        prompt: 提问, 默认中文描述图片内容。
    Returns:
        模型输出的文字。
    """
    llm = _load()
    out = llm.create_chat_completion(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_path}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        max_tokens=300,
        temperature=0.3,
    )
    return (out["choices"][0]["message"]["content"] or "").strip()