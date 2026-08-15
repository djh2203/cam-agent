# coding=utf-8
"""HTTP 网关: 让 AI(或任何客户端)通过 REST 接口控制摄像头"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .camera import Camera, CameraError
from .tts import text_to_g711a, beep_g711a

MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "media")
os.makedirs(MEDIA_DIR, exist_ok=True)


def _json(body, status=200):
    data = json.dumps(body, ensure_ascii=False).encode()
    return (status, {"Content-Type": "application/json; charset=utf-8"}, data)


def _text(body, status=200, ctype="text/plain; charset=utf-8"):
    data = body.encode()
    return (status, {"Content-Type": ctype}, data)


class GateHandler(BaseHTTPRequestHandler):
    camera = None  # 由 server 注入
    cfg = None

    # ---------- 工具 ----------
    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _respond(self, resp):
        status, headers, body = resp
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, **kw):
        self._respond(_json({"ok": True, **kw}))

    def _err(self, msg, status=400):
        self._respond(_json({"ok": False, "error": str(msg)}, status=status))

    def log_message(self, fmt, *args):
        print(f"[gateway] {self.address_string()} {fmt % args}")

    # ---------- 路由 ----------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/health":
                self._ok(online=self.camera.online, ip=self.camera.ip)
            elif path == "/info":
                self._info()
            elif path == "/frame.jpg":
                self._frame()
            else:
                self._err("未找到路由", 404)
        except CameraError as e:
            self._err(e, 500)
        except Exception as e:
            self._err(f"内部错误: {e}", 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/login":
                self._login()
            elif path == "/logout":
                self._logout()
            elif path == "/ptz":
                self._ptz()
            elif path == "/ptz/stop":
                self._ptz_stop()
            elif path == "/preset":
                self._preset()
            elif path == "/capture":
                self._capture()
            elif path == "/speak":
                self._speak()
            elif path == "/beep":
                self._beep()
            else:
                self._err("未找到路由", 404)
        except CameraError as e:
            self._err(e, 500)
        except Exception as e:
            self._err(f"内部错误: {e}", 500)

    # ---------- 接口实现 ----------
    def _info(self):
        cam = self.camera
        if not cam.loginID:
            self._err("未登录", 401)
            return
        dev = cam.device_info
        self._ok(
            ip=cam.ip,
            channel=cam.channel,
            channel_num=dev.nChanNum if dev else 0,
            serial=dev.sSerialNumber.decode(errors="ignore") if dev else "",
            device_type=dev.nDVRType if dev else 0,
        )

    def _frame(self):
        frame = getattr(self.server, "last_frame", None)
        if not frame:
            self._err("暂无帧, 请先 /capture", 404)
            return
        self._respond(
            (200, {"Content-Type": "image/jpeg"}, frame)
        )

    def _login(self):
        data = self._read_json()
        ip = data.get("ip") or self.camera.ip
        port = data.get("port") or self.camera.port
        user = data.get("username") or self.camera.username
        pwd = data.get("password") or self.camera.password
        if self.camera.loginID:
            self.camera.logout()
        self.camera.ip = ip
        self.camera.port = int(port)
        self.camera.username = user
        self.camera.password = pwd
        if self.camera.login():
            self._ok(message="登录成功")
        else:
            self._err("登录失败", 401)

    def _logout(self):
        self.camera.logout()
        self._ok(message="已登出")

    def _ptz(self):
        data = self._read_json()
        action = data.get("action")
        speed = data.get("speed", 5)
        seconds = data.get("seconds", 0.5)
        if not action:
            self._err("缺少 action")
            return
        self.camera.ptz_move(action, speed, seconds)
        self._ok(action=action, speed=speed, seconds=seconds)

    def _ptz_stop(self):
        data = self._read_json()
        action = data.get("action", "up")
        self.camera.ptz_stop(action)
        self._ok(action=action, message="已停止")

    def _preset(self):
        data = self._read_json()
        op = data.get("op")
        index = data.get("index")
        if op not in ("set", "goto", "del") or index is None:
            self._err("需要 op(set/goto/del) 和 index")
            return
        self.camera.ptz_preset(op, index)
        self._ok(op=op, index=index)

    def _capture(self):
        frame = self.camera.capture()
        self.server.last_frame = frame
        fname = f"cap_{int(time.time()*1000)}.jpg"
        fpath = os.path.join(MEDIA_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(frame)
        self._ok(
            path=fpath,
            url=f"/frame.jpg",
            size=len(frame),
        )

    def _speak(self):
        data = self._read_json()
        text = data.get("text")
        codec = data.get("codec", "mulaw")
        if not text:
            self._err("缺少 text")
            return
        out = os.path.join(MEDIA_DIR, f"speak_{int(time.time()*1000)}.{codec}")
        path, duration = text_to_g711a(text, self.cfg, out, codec=codec)
        self.camera.speak_file(path, codec=codec)
        self._ok(text=text, codec=codec, duration=round(duration, 2), message="已播放")

    def _beep(self):
        out = os.path.join(MEDIA_DIR, f"beep_{int(time.time()*1000)}.g711a")
        path, seconds = beep_g711a(out)
        self.camera.speak_file(path, codec="alaw")
        self._ok(message="提示音已播放", duration=seconds)


def create_server(camera, cfg, host, port):
    class S(ThreadingHTTPServer):
        def __init__(self, *a, **kw):
            self.last_frame = None
            super().__init__(*a, **kw)

    GateHandler.camera = camera
    GateHandler.cfg = cfg
    server = S((host, port), GateHandler)
    return server


def run_gateway(camera, cfg, host="127.0.0.1", port=8080):
    server = create_server(camera, cfg, host, port)
    print(f"[gateway] HTTP 服务已启动: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[gateway] 正在退出...")
    finally:
        server.server_close()


if __name__ == "__main__":
    from .config import load_config

    cfg = load_config()
    cam = Camera(
        cfg["camera"]["ip"],
        cfg["camera"]["port"],
        cfg["camera"]["username"],
        cfg["camera"]["password"],
        cfg["camera"]["channel"],
    )
    cam.login()
    run_gateway(cam, cfg, cfg["server"]["host"], int(cfg["server"]["port"]))