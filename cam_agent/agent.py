# coding=utf-8
"""AI agent: 通过 OpenAI 兼容接口的 function calling 让大模型指挥摄像头.

大模型可调用以下工具(对应网关 REST 接口):
  - camera_status / camera_info  查询摄像头状态
  - ptz_move / ptz_stop          云台移动
  - preset                       预置点
  - capture                      抓图(返回图片 URL, 也可立即传给视觉模型分析)
  - analyze                      抓图并交给视觉模型描述画面
  - speak                        文字转语音通过扬声器播报
"""
import base64
import json
import os
import sys
import urllib.request
import urllib.error

from .config import load_config

SYSTEM_PROMPT = """你是一个连接到大华(Dahua)网络摄像头的智能控制助手。
你可以转动云台、切换预置点、抓取画面、查看画面内容、通过摄像头扬声器说话。

**最重要规则: 绝对不要编造任何 IP 地址、密码或设备信息。**

规则:
1. 用中文回答。
2. 云台移动时, speed 用 3~6 之间, seconds 用 0.3~0.8。要持续移动就分多次调用。
3. 想了解当前看到什么, 直接调 analyze。
4. 播报语音时 text 用简洁中文口语, 一次不超过 60 字。
5. 抓图/分析时如果画面太暗或角度不对, 可以调整云台后再看。
6. 不知道有什么摄像头时, 必须先调 search_cameras 搜索, 或调 list_cameras 查看已保存的设备。绝对不要凭空编造 IP。
7. select_camera 只需传 ip 参数, 密码已保存在本地 cameras.json 中, 无需每次提供。
8. 工具返回错误时, 根据错误信息修正参数重试。常见错误及修正:
   - "请先选择摄像头" → 调 list_cameras 检查, 或提示用户切换摄像头
   - "未找到预置点" → 调 camera_info 查看有多少预置点, 用有效编号重试
   - "参数错误" → 检查参数格式, 用正确类型重试
   不要放弃, 至少重试 2 次再向用户报错。
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "camera_status",
            "description": "查询摄像头在线状态",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "camera_info",
            "description": "查询摄像头详细信息(序列号/通道数)",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ptz_move",
            "description": "控制云台向某个方向移动(移动一段时间后自动停止)",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right", "leftup", "leftdown",
                                 "rightup", "rightdown", "zoomin", "zoomout"],
                        "description": "移动方向",
                    },
                    "speed": {"type": "integer", "minimum": 1, "maximum": 8, "description": "速度1-8"},
                    "seconds": {"type": "number", "description": "移动时长(秒), 0.3-2"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ptz_stop",
            "description": "立即停止云台所有移动",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preset",
            "description": "设置/调用/删除云台预置点",
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["set", "goto", "del"]},
                    "index": {"type": "integer", "description": "预置点序号, 从1开始"},
                },
                "required": ["op", "index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture",
            "description": "抓取一帧画面并保存, 返回图片URL",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze",
            "description": "抓取当前画面并交给视觉大模型识别, 返回画面描述",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "想了解画面的什么问题, 可省略"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "speak",
            "description": "把一段文字通过摄像头扬声器播报出来",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "要播报的文字"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_cameras",
            "description": "局域网搜索摄像头设备",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_camera",
            "description": "切换到指定IP的摄像头",
            "parameters": {
                "type": "object",
                "properties": {
                    "ip": {"type": "string", "description": "摄像头IP地址"},
                    "password": {"type": "string", "description": "密码"},
                },
                "required": ["ip"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_cameras",
            "description": "查看当前已连接的摄像头列表",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


class GatewayClient:
    """调用本地网关 HTTP 接口"""

    def __init__(self, base):
        self.base = base.rstrip("/")

    def _post(self, path, payload=None):
        data = json.dumps(payload or {}).encode()
        req = urllib.request.Request(
            self.base + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:
                return {"ok": False, "error": f"HTTP {e.code}"}

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=60) as resp:
                if "image" in resp.headers.get("Content-Type", ""):
                    return resp.read()
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code}"}

    # 工具实现
    def camera_status(self):
        return self._get("/health")

    def camera_info(self):
        return self._get("/info")

    def ptz_move(self, action, speed=5, seconds=0.5):
        return self._post("/ptz", {"action": action, "speed": speed, "seconds": seconds})

    def ptz_stop(self):
        return self._post("/ptz/stop", {})

    def preset(self, op, index):
        return self._post("/preset", {"op": op, "index": index})

    def capture(self):
        return self._post("/capture", {})

    def frame(self):
        return self._get("/frame.jpg")

    def speak(self, text):
        return self._post("/speak", {"text": text, "codec": "mulaw"})

    def analyze(self, question=None):
        """抓图 + 本地视觉分析画面"""
        cap = self._post("/capture", {})
        if not cap.get("ok"):
            return cap
        from .vision import analyze_image

        path = cap["path"]
        if question:
            result = analyze_image(path, question)
        else:
            result = analyze_image(path)
        return {"ok": True, "path": path, "result": result}

    def search_cameras(self):
        return self._get("/cameras/search")

    def select_camera(self, ip, password=None):
        payload = {"ip": ip}
        if password:
            payload["password"] = password
        return self._post("/cameras/select", payload)

    def list_cameras(self):
        return self._get("/cameras")


class AIAgent:
    def __init__(self, cfg, gateway_base="http://127.0.0.1:8080"):
        self.cfg = cfg
        self.gateway = GatewayClient(gateway_base)
        # 意图理解走本地 llama-server (和 voice.py 一致)
        self.base_url = os.environ.get("VISION_BASE_URL", "http://127.0.0.1:8081/v1").rstrip("/")
        self.api_key = ""
        self.chat_model = "qwen"
        self.vision_model = "qwen"

        self._tools = {t["function"]["name"]: getattr(self.gateway, t["function"]["name"]) for t in TOOLS}

    # ---------- 底层调用 ----------
    def _chat(self, messages, model=None, tools=None):
        url = self.base_url + "/chat/completions"
        payload = {"model": model or self.chat_model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())

    def _vision(self, image_bytes, question):
        """调用视觉模型分析画面"""
        b64 = base64.b64encode(image_bytes).decode()
        url = self.base_url + "/chat/completions"
        payload = {
            "model": self.vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question or "描述一下这个画面里有什么?"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }
            ],
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"]

    # ---------- 工具执行 ----------
    def _call_tool(self, name, args):
        print(f"  [tool] {name}{args}", flush=True)
        if name == "analyze":
            cap = self.gateway.capture()
            if not cap.get("ok"):
                return json.dumps({"error": f"抓图失败: {cap.get('error', '未知错误')}. 请先 list_cameras 检查是否有摄像头连接。"}, ensure_ascii=False)
            frame = self.gateway.frame()
            result = self._vision(frame, args.get("question", ""))
            return result
        fn = self._tools.get(name)
        if fn is None:
            return json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False)
        try:
            result = fn(**args)
        except TypeError as e:
            # 参数缺失或类型错误
            result = {"error": f"参数错误: {e}. 请检查工具定义中的参数要求。"}
        except Exception as e:
            result = {"error": str(e)}
        return json.dumps(result, ensure_ascii=False)

    # ---------- 主循环 ----------
    def run(self, user_input, max_steps=15):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.append({"role": "user", "content": user_input})

        for _ in range(max_steps):
            resp = self._chat(messages, tools=TOOLS)
            msg = resp["choices"][0]["message"]

            if msg.get("tool_calls"):
                messages.append(msg)
                for tc in msg["tool_calls"]:
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except Exception:
                        args = {}
                    result = self._call_tool(name, args)
                    messages.append(
                        {"role": "tool", "tool_call_id": tc["id"], "content": result}
                    )
            else:
                return msg.get("content", "")

        return "已达到最大步骤数, 未完成。"


def main():
    cfg = load_config()

    # 自动拉起 gateway
    import subprocess, time, socket
    gw_host = cfg.get("server", "host", fallback="127.0.0.1")
    gw_port = cfg.get("server", "port", fallback="8080")
    def _gw_alive():
        try:
            with socket.create_connection((gw_host, int(gw_port)), timeout=1):
                return True
        except Exception:
            return False
    if not _gw_alive():
        print(f"正在启动 gateway ({gw_host}:{gw_port})...")
        subprocess.Popen(
            [sys.executable, "-m", "cam_agent", "gateway"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(10):
            time.sleep(0.5)
            if _gw_alive():
                print("gateway 已启动")
                break
        else:
            print("gateway 启动超时, 部分功能可能不可用")

    agent = AIAgent(cfg, gateway_base=f"http://{gw_host}:{gw_port}")
    print("=" * 50)
    print("AI 摄像头控制助手已就绪。输入指令, 例如:")
    print('  "摄像头往右转" / "现在看到什么" / "对摄像头说欢迎光临" / "去预置点1"')
    print("输入 exit 退出")
    print("=" * 50)
    while True:
        try:
            user_input = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            break
        try:
            reply = agent.run(user_input)
            print(f"助手> {reply}")
        except urllib.error.HTTPError as e:
            print(f"助手> API 调用失败: HTTP {e.code} {e.read().decode(errors='ignore')}")
        except Exception as e:
            print(f"助手> 出错: {e}")


if __name__ == "__main__":
    main()