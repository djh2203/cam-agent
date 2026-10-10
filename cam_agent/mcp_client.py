# coding=utf-8
"""MCP client: 连接 mcp-dahua-camera 仓库的 MCP server, 把设备能力暴露为本地同步函数.

cam-agent 不再直接 import mcp_server.HANDLERS, 而是通过本模块走标准 MCP 协议
调用独立的 mcp-dahua-camera 仓库(stdin/stdout), 实现两个仓库干净解耦。

用法:
    client = MCPCameraClient()          # 自动启动 mcp-dahua-camera 子进程
    client.capture()                    # 同步调用工具, 返回 dict
    client.call("ptz_move", {"action": "right"})
"""
import asyncio
import json
import os
import threading

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:
    ClientSession = None
    StdioServerParameters = None
    stdio_client = None

# mcp-dahua-camera 仓库路径: 环境变量覆盖, 默认同级的 mcp-dahua-camera
MCP_REPO_DIR = os.environ.get(
    "MCP_DAHUA_CAMERA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "mcp-dahua-camera"),
)


class MCPCameraClient:
    """通过 stdio 连接 mcp-dahua-camera 的 MCP server, 同步调用工具.

    后台线程运行 asyncio 事件循环, 持有常驻的 ClientSession, 提供线程安全的 call().
    """

    def __init__(self, repo_dir=None):
        if ClientSession is None:
            raise RuntimeError("未安装 mcp 库")
        self.repo_dir = os.path.abspath(repo_dir or MCP_REPO_DIR)
        if not os.path.exists(os.path.join(self.repo_dir, "pyproject.toml")):
            raise RuntimeError(f"mcp-dahua-camera 仓库不存在: {self.repo_dir}")

        self._loop = asyncio.new_event_loop()
        self._session = None
        self._tools = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._ready = threading.Event()
        self._thread.start()
        if not self._ready.wait(timeout=60):
            raise RuntimeError("MCP client 启动超时")
        if self._session is None:
            raise RuntimeError("MCP server 启动失败, 请检查 mcp-dahua-camera 仓库路径")

    # ---------- 后台事件循环 ----------
    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect())

    async def _connect(self):
        try:
            params = StdioServerParameters(
                command="uv",
                args=[
                    "run",
                    "--project",
                    self.repo_dir,
                    "python",
                    "-m",
                    "mcp_server",
                ],
                cwd=self.repo_dir,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    result = await session.list_tools()
                    self._tools = [t.name for t in result.tools]
                    self._ready.set()
                    # 常驻等待, 保持连接
                    await asyncio.Event().wait()
        except Exception as e:  # noqa: BLE001
            print(f"[MCP client] 连接 mcp-dahua-camera 失败: {e}", flush=True)
            self._ready.set()

    # ---------- 同步调用 ----------
    def call(self, tool_name, args=None):
        """同步调用工具, 返回 dict(工具返回的 JSON 解析结果)"""
        args = args or {}
        if self._session is None:
            raise RuntimeError("MCP server 未连接")
        with self._lock:
            future = asyncio.run_coroutine_threadsafe(
                self._session.call_tool(tool_name, args), self._loop
            )
            result = future.result(timeout=120)
        # 解析 content[0].text
        texts = [c.text for c in result.content if hasattr(c, "text")]
        if not texts:
            return {"ok": False, "error": "无返回内容"}
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return {"ok": True, "result": texts[0]}

    # ---------- 便捷方法 ----------
    def tools(self):
        return list(self._tools)

    def ptz_move(self, action, speed=5, seconds=0.5):
        return self.call("ptz_move", {"action": action, "speed": speed, "seconds": seconds})

    def ptz_stop(self):
        return self.call("ptz_stop")

    def ptz_abs_move(self, pan_deg, tilt_deg=None):
        return self.call("ptz_abs_move", {"pan_deg": pan_deg, "tilt_deg": tilt_deg})

    def ptz_get_position(self):
        return self.call("ptz_get_position")

    def preset(self, op, index=1):
        return self.call("preset", {"op": op, "index": index})

    def capture(self):
        return self.call("capture")

    def record_audio(self, seconds=5):
        return self.call("record_audio", {"seconds": seconds})

    def play_audio(self, path, codec="mulaw", sample_rate=16000):
        return self.call("play_audio", {"path": path, "codec": codec, "sample_rate": sample_rate})

    def camera_status(self):
        return self.call("camera_status")

    def camera_info(self):
        return self.call("camera_info")

    def search_devices(self, timeout=8):
        return self.call("search_devices", {"timeout": timeout})

    def select_camera(self, ip, port=None, username=None, password=None, channel=None):
        args = {"ip": ip}
        if port is not None:
            args["port"] = port
        if username is not None:
            args["username"] = username
        if password is not None:
            args["password"] = password
        if channel is not None:
            args["channel"] = channel
        return self.call("select_camera", args)

    def list_cameras(self):
        return self.call("list_cameras")


_client = None


def get_client():
    """单例 MCP client"""
    global _client
    if _client is None:
        _client = MCPCameraClient()
    return _client


class CameraFacade:
    """兼容旧代码(Camera 对象)接口的门面: 内部全部通过 MCP client 调 mcp-dahua-camera.

    属性: ip/channel/online/loginID/device_info; 方法: select_camera/search_devices/
    list_cameras/capture/ptz_move/ptz_stop/ptz_preset/speak_file/cleanup。
    未选中摄像头时 online=False、loginID=0(MCP server 是常驻进程, 自己不管理登录)。
    """

    def __init__(self, client=None):
        self._client = client if client is not None else get_client()
        self.ip = None
        self.port = None
        self.username = None
        self.password = None
        self.channel = 0
        self.loginID = 0  # 未选摄像头即未登录, 由 _init_from_info 置 1
        self._online = False
        self.device_info = None
        # 尝试获取当前摄像头信息(可能还没选)
        info = self._safe_call("camera_info", {})
        if info.get("ok"):
            self._init_from_info(info)

    def _init_from_info(self, info):
        """从 camera_info 结果初始化属性."""
        self.ip = info.get("ip")
        self.channel = info.get("channel_num", 0)
        self.loginID = 1
        self._online = True
        self.device_info = type("DevInfo", (), {
            "nChanNum": info.get("channel_num", 1),
            "sSerialNumber": info.get("serial", "").encode(),
            "nDVRType": info.get("device_type", 0),
            "model": info.get("model", ""),
            "software_version": info.get("software_version", ""),
            "hardware_version": info.get("hardware_version", ""),
            "smart_track_supported": info.get("smart_track_supported", False),
            "smart_track_enabled": info.get("smart_track_enabled", False),
            "motion_detect_enabled": info.get("motion_detect_enabled", False),
            "tracking_zoom": info.get("tracking_zoom", False),
        })()

    def select_camera(self, ip, port=None, username=None, password=None, channel=None):
        """切换摄像头, 成功后重新初始化属性."""
        r = self._client.select_camera(ip, port, username, password, channel)
        if r.get("ok"):
            self._init_from_info(r)
        return r

    def search_devices(self, timeout=8):
        return self._client.search_devices(timeout)

    def list_cameras(self):
        return self._client.list_cameras()

    def _safe_call(self, name, args=None):
        try:
            return self._client.call(name, args or {})
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------- Camera 兼容接口 ----------
    def cleanup(self):
        pass

    @property
    def online(self):
        return self._online

    def capture(self):
        r = self._safe_call("capture", {})
        if not r.get("ok"):
            raise RuntimeError(r.get("error", "抓图失败"))
        with open(r["path"], "rb") as f:
            return f.read()

    def ptz_move(self, action, speed=5, seconds=0.5):
        r = self._safe_call("ptz_move", {"action": action, "speed": speed, "seconds": seconds})
        if not r.get("ok"):
            raise RuntimeError(r.get("error", "云台移动失败"))

    def ptz_stop(self, action="up", speed=5):
        self._safe_call("ptz_stop", {})

    def ptz_preset(self, op, index, name=None):
        r = self._safe_call("preset", {"op": op, "index": index})
        if not r.get("ok"):
            raise RuntimeError(r.get("error", "预置点操作失败"))

    def speak_file(self, g711a_path, sample_rate=16000, audio_bit=8, codec="mulaw"):
        r = self._safe_call(
            "play_audio",
            {"path": g711a_path, "codec": codec, "sample_rate": sample_rate},
        )
        if not r.get("ok"):
            raise RuntimeError(r.get("error", "播放失败"))
