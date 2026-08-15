# coding=utf-8
"""MCP server: 把大华摄像头控制能力暴露为 MCP tools (low-level API).

任何支持 MCP 的 agent(Claude Desktop / Cursor / opencode 等)都可以直接接入,
无需把 apikey 填到 agent 里。摄像头工具: 云台/预置点/抓图/语音播报。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
)

from camgate.config import load_config
from camgate.camera import Camera, CameraError
from camgate.tts import text_to_g711a

cfg = load_config()
MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

_cam = None


def _get_cam():
    global _cam
    if _cam is None:
        _cam = Camera(
            cfg["camera"]["ip"],
            cfg["camera"]["port"],
            cfg["camera"]["username"],
            cfg["camera"]["password"],
            cfg["camera"]["channel"],
        )
        _cam.login()
    return _cam


def _ok(**kw):
    return {"ok": True, **kw}


def _fail(e):
    return {"ok": False, "error": str(e)}


def _result(obj) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(obj, ensure_ascii=False))])


# ---------- 工具实现 ----------
def t_camera_status(args):
    cam = _get_cam()
    return _ok(online=cam.online, ip=cam.ip)


def t_camera_info(args):
    cam = _get_cam()
    dev = cam.device_info
    if not dev:
        return _fail("未登录")
    return _ok(
        ip=cam.ip,
        channel_num=dev.nChanNum,
        serial=dev.sSerialNumber.decode(errors="ignore"),
        device_type=dev.nDVRType,
    )


def t_ptz_move(args):
    action = args.get("action")
    speed = int(args.get("speed", 5))
    seconds = float(args.get("seconds", 0.5))
    try:
        _get_cam().ptz_move(action, speed, seconds)
        return _ok(action=action, speed=speed, seconds=seconds)
    except CameraError as e:
        return _fail(e)


def t_ptz_stop(args):
    try:
        _get_cam().ptz_stop()
        return _ok(message="已停止")
    except CameraError as e:
        return _fail(e)


def t_ptz_abs_move(args):
    pan = float(args.get("pan_deg"))
    try:
        _get_cam().ptz_abs_move(pan)
        return _ok(pan_deg=pan, message="已定位(仅水平)。上下调节请用 ptz_move 的 up/down")
    except CameraError as e:
        return _fail(e)


def t_ptz_get_position(args):
    try:
        pan, tilt = _get_cam().ptz_get_position()
        return _ok(pan_deg=round(pan, 1), tilt_deg=round(tilt, 1))
    except CameraError as e:
        return _fail(e)


def t_preset(args):
    op = args.get("op")
    index = int(args.get("index", 1))
    try:
        _get_cam().ptz_preset(op, index)
        return _ok(op=op, index=index)
    except CameraError as e:
        return _fail(e)


def t_capture(args):
    try:
        frame = _get_cam().capture()
        fname = f"cap_{int(time.time()*1000)}.jpg"
        fpath = os.path.join(MEDIA_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(frame)
        return _ok(path=fpath, size=len(frame))
    except CameraError as e:
        return _fail(e)


def t_speak(args):
    text = args.get("text", "")
    if not text:
        return _fail("缺少 text")
    try:
        out = os.path.join(MEDIA_DIR, f"speak_{int(time.time()*1000)}.mulaw")
        path, duration = text_to_g711a(text, cfg, out, codec="mulaw")
        _get_cam().speak_file(path, codec="mulaw")
        return _ok(duration=round(duration, 2), message="已播报")
    except Exception as e:
        return _fail(e)


# ---------- 工具元数据 ----------
TOOLS = [
    Tool(
        name="camera_status",
        description="查询摄像头在线状态",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="camera_info",
        description="查询摄像头详细信息(序列号/通道数/设备类型)",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="ptz_move",
        description="控制云台移动。action: up/down/left/right/leftup/leftdown/rightup/rightdown/zoomin/zoomout; speed: 1-255(255为满速, 上下调节建议用满速才明显); seconds: 移动时长(秒)。注意: 上下(tilt)只能用它, 绝对定位不管上下。",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["up", "down", "left", "right", "leftup", "leftdown", "rightup", "rightdown", "zoomin", "zoomout"]},
                "speed": {"type": "integer", "minimum": 1, "maximum": 255},
                "seconds": {"type": "number", "minimum": 0.2, "maximum": 10},
            },
            "required": ["action"],
        },
    ),
    Tool(
        name="ptz_stop",
        description="立即停止云台所有移动",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="ptz_abs_move",
        description="绝对定位水平角(pan_deg): 0=正面/90=左/180=背面/270=右, 0~360精准。此设备绝对定位不生效于垂直轴, 需上下调节请用 ptz_move 的 up/down(speed 建议 255)。",
        inputSchema={
            "type": "object",
            "properties": {
                "pan_deg": {"type": "number", "minimum": 0, "maximum": 360},
            },
            "required": ["pan_deg"],
        },
    ),
    Tool(
        name="ptz_get_position",
        description="读取云台当前水平/垂直角度",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="preset",
        description="操作云台预置点。op: set(设置当前位)/goto(转到)/del(删除); index: 预置点序号",
        inputSchema={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["set", "goto", "del"]},
                "index": {"type": "integer", "minimum": 1},
            },
            "required": ["op", "index"],
        },
    ),
    Tool(
        name="capture",
        description="抓取一帧画面, 返回保存路径和文件大小",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="speak",
        description="把文字通过摄像头扬声器播报出来(中文优先)",
        inputSchema={
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要播报的文字"}},
            "required": ["text"],
        },
    ),
]

HANDLERS = {
    "camera_status": t_camera_status,
    "camera_info": t_camera_info,
    "ptz_move": t_ptz_move,
    "ptz_stop": t_ptz_stop,
    "ptz_abs_move": t_ptz_abs_move,
    "ptz_get_position": t_ptz_get_position,
    "preset": t_preset,
    "capture": t_capture,
    "speak": t_speak,
}


async def _list_tools(ctx, params: PaginatedRequestParams | None):
    return ListToolsResult(tools=TOOLS)


async def _call_tool(ctx, params):
    import anyio

    name = params.name
    args = params.arguments or {}
    handler = HANDLERS.get(name)
    if handler is None:
        return _result(_fail(f"未知工具: {name}"))
    try:
        result = await anyio.to_thread.run_sync(handler, args)
    except Exception as e:
        result = _fail(e)
    return _result(result)


def main():
    import anyio

    server = Server("camgate", on_list_tools=_list_tools, on_call_tool=_call_tool)

    async def _run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(_run)


if __name__ == "__main__":
    main()