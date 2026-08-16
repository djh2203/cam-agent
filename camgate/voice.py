# coding=utf-8
"""语音对话助手: 命令行启动后一直监听摄像头麦克风,
识别到说话 -> 云 LLM 理解意图并 function calling 控制云台/看图 -> 扬声器播报回复.

用法: uv run camgate voice
依赖 config.ini 的 [ai] 段(OpenAI 兼容 base_url / api_key / chat_model)。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .config import load_config
from .asr import transcribe_pcm16

MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

# 单实例锁文件: 防止多个 voice 进程同时抢对讲通道
_LOCK_PATH = os.path.join(MEDIA_DIR, "voice.lock")

SYSTEM_PROMPT = """你是连接到大华摄像头的智能语音助手。用户通过摄像头麦克风对你喊话,你的回复会通过摄像头扬声器播报。
你能: 转动云台、切换预置点、抓取画面并看内容。
规则:
1. 用户让移动/转向/看某方向 → 必须调用工具 ptz_move; 去预置点 → 必须调用 preset; 想看画面/问看到什么 → 必须调用 analyze_image。
2. 不要直接文字回答, 必须调用对应工具(除非是打招呼/闲聊)。
3. ptz_move: action 用 up/down/left/right, speed 用 5, seconds 用 0.5。
4. 拿到工具返回结果后, 用简洁中文口语一句话回复, 不超过30字, 不要编造。"""

# 精简工具集: 只留本地模型稳定触发的工具, 工具越多推理越慢
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ptz_move",
            "description": "控制云台向某个方向移动一段时间。用户说转向/移动/看哪个方向时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right", "leftup", "leftdown", "rightup", "rightdown"],
                        "description": "移动方向(上下用 up/down, 左右用 left/right)",
                    },
                    "speed": {"type": "integer", "minimum": 1, "maximum": 255, "description": "速度, 默认5"},
                    "seconds": {"type": "number", "description": "移动时长(秒), 默认0.5"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preset",
            "description": "调用云台预置点(转到预先存好的位置)。用户说去预置点/去某位置时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["goto"], "description": "操作, 固定 goto"},
                    "index": {"type": "integer", "description": "预置点序号"},
                },
                "required": ["op", "index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_image",
            "description": "抓取当前画面并用本地视觉模型识别, 返回画面内容描述。用户问'看到什么/有没有人'时调用",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


class VoiceAssistant:
    def __init__(self, cfg, cam):
        self.cfg = cfg
        self.cam = cam
        # 意图理解走本地 llama-server (OpenAI 兼容, 快且稳定), 可用 VISION_BASE_URL 覆盖
        self.base_url = os.environ.get("VISION_BASE_URL", "http://127.0.0.1:8081/v1").rstrip("/")
        self.api_key = ""
        self.model = "qwen"

    # ---------- 本地 LLM (llama-server) ----------
    def _chat(self, messages):
        from .vision_server import ensure_running, health

        if not health():
            ensure_running()
        url = self.base_url + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "max_tokens": 80,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())

    # ---------- 工具执行(复用 mcp_server 的 handler) ----------
    def _run_tool(self, name, args):
        from .mcp_server import HANDLERS

        handler = HANDLERS.get(name)
        if handler is None:
            return {"error": f"未知工具 {name}"}
        print(f"  [tool] {name}{args}", flush=True)
        try:
            return handler(args)
        except Exception as e:
            return {"error": str(e)}

    # ---------- 一轮对话 ----------
    def process(self, heard: str) -> str:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.append({"role": "user", "content": f"用户说: {heard}"})

        for _ in range(6):
            resp = self._chat(messages)
            msg = resp["choices"][0]["message"]

            if msg.get("tool_calls"):
                messages.append(msg)
                for tc in msg["tool_calls"]:
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except Exception:
                        args = {}
                    result = self._run_tool(name, args)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result, ensure_ascii=False)})
            else:
                return (msg.get("content") or "").strip()

        return "我有点忙不过来了,请再说一遍。"

    # ---------- 播报 ----------
    def speak(self, text):
        if not text:
            return
        from .tts import text_to_g711a

        out = os.path.join(MEDIA_DIR, f"reply_{int(time.time()*1000)}.mulaw")
        path, _ = text_to_g711a(text, self.cfg, out, codec="mulaw")
        self.cam.speak_file(path, codec="mulaw")


def main():
    cfg = load_config()
    # 意图理解走本地 llama-server, 确认可用
    from .vision_server import health, ensure_running

    if not health():
        print("正在启动本地视觉/对话服务 (llama-server)...")
        ensure_running()
    if not health():
        print("[!] 本地 LLM 服务启动失败, 请检查 vendor/llama/llama-server 是否就绪")
        return

    # 单实例锁: 已有 voice 进程在跑则退出(避免多进程抢对讲通道)
    if os.path.exists(_LOCK_PATH):
        try:
            with open(_LOCK_PATH) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)  # 0 表示只检查进程是否存在
            print(f"[!] 已有 voice 进程在运行 (PID {pid})。请先停止它再启动。")
            return
        except (ValueError, ProcessLookupError):
            pass  # 锁文件残留(进程已死), 继续启动
    with open(_LOCK_PATH, "w") as f:
        f.write(str(os.getpid()))

    from .mcp_server import _get_cam
    from .camera import CameraError

    cam = _get_cam()
    assistant = VoiceAssistant(cfg, cam)

    print("=" * 50, flush=True)
    print("语音助手已启动, 一直监听摄像头麦克风...", flush=True)
    print("  - 对摄像头说话(如: 往右转 / 看看有什么 / 去预置点1)", flush=True)
    print("  - 说完会通过扬声器回应你", flush=True)
    print("  - Ctrl+C 退出", flush=True)
    print("=" * 50, flush=True)

    last_said = 0.0
    _last_text = ""
    round_no = 0
    while True:
        try:
            round_no += 1
            print(f"[{round_no}] 监听中...", flush=True)
            pcm, rate, bit = cam.record_audio(6, on_retry=lambda w: print(f"[!] 对讲通道被占用, 等待释放({int(w)}s)...", flush=True))
            text = transcribe_pcm16(pcm, rate)
            if not text:
                continue
            # 简单的防重复: 1.5 秒内相同的识别结果跳过
            now = time.time()
            if now - last_said < 1.5 and text == _last_text:
                continue
            _last_text = text
            last_said = now

            print(f"[听] {text}", flush=True)
            reply = assistant.process(text)
            print(f"[说] {reply}", flush=True)
            try:
                assistant.speak(reply)
            except Exception as e:
                print(f"[说] 播报失败: {e}", flush=True)
        except CameraError as e:
            print(f"[!] {e}", flush=True)
            time.sleep(2)
        except KeyboardInterrupt:
            print("\n已退出")
            break
        except Exception as e:
            print(f"[!] {e}", flush=True)
            time.sleep(1)

    try:
        os.remove(_LOCK_PATH)
    except OSError:
        pass


if __name__ == "__main__":
    main()