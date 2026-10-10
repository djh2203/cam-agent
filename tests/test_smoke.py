# coding=utf-8
"""无硬件冒烟自检: `python tests/test_smoke.py`(也可 pytest)。
锁住三处已修 bug, 回退就会失败:
  1. agent.py 自动拉起 gateway 用 sys.executable
  2. vision_server.stop() 在服务在线时也必须真的停止
  3. CameraFacade 未选摄像头时不能谎报 online/loginID
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeResponse:
    def read(self):
        return b'{"total_slots": 1}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeClient:
    """假 MCP client: 只回答 camera_info / select_camera"""

    def __init__(self, info_ok):
        self.info_ok = info_ok

    def call(self, name, args=None):
        if name == "camera_info":
            if self.info_ok:
                return {"ok": True, "ip": "10.0.0.9"}
            return {"ok": False, "error": "未选择摄像头"}
        return {"ok": True}

    def select_camera(self, ip, port=None, username=None, password=None, channel=None):
        return {"ok": True, "ip": ip}


def test_agent_has_sys():
    """agent.main() 自动拉起 gateway 时要用 sys.executable"""
    from cam_agent import agent

    assert hasattr(agent, "sys"), "agent.py 缺 import sys → 自动拉起 gateway 会 NameError"


def test_vision_server_stop_kills_online_server():
    """服务在线时 stop() 也必须下发停止命令(旧实现直接 return, 什么也不做)"""
    from cam_agent import vision_server as vs

    ran = []
    vs.urllib.request.urlopen = lambda *a, **kw: _FakeResponse()
    vs.os.system = lambda cmd: ran.append(cmd)
    vs.stop(5999)
    assert ran and "5999/tcp" in ran[0], f"stop() 未下发停止命令: {ran}"


def test_facade_does_not_lie_about_state():
    """未选摄像头 → online False / loginID 0 / ip None; 选了之后才 True"""
    from cam_agent.mcp_client import CameraFacade

    cam = CameraFacade(client=_FakeClient(info_ok=False))
    assert cam.online is False and cam.loginID == 0 and cam.ip is None, "未选摄像头却报在线"

    cam.select_camera("10.0.0.9")
    assert cam.online is True and cam.loginID == 1 and cam.ip == "10.0.0.9", "选了摄像头却没置在线"


def test_tts_output_is_16k():
    """TTS 产出的采样率必须真的等于 TTS_RATE。

    设备按 16kHz 消费音频: 产出和声明不一致, 扬声器就等于只收到一半数据量 -> 卡顿/变速。
    用 ffmpeg 造正弦, 不依赖网络。
    """
    import shutil
    import subprocess
    import tempfile

    from cam_agent import tts

    assert tts.TTS_RATE == 16000, "摄像头扬声器按 16kHz 消费, 改了要同步 camera.py 的 speak_file"
    if not shutil.which("ffmpeg"):
        return  # ffmpeg 是运行时依赖, 没装就跳过

    with tempfile.TemporaryDirectory() as d:
        wav = os.path.join(d, "sine.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=1:sample_rate=48000", "-ac", "1", wav],
            check=True, capture_output=True,
        )
        raw = tts._ffmpeg_to_raw_codec(wav, tts.TTS_RATE, "mulaw")
        secs = len(raw) / tts.TTS_RATE
        assert 0.9 < secs < 1.1, f"1 秒音频产出 {secs:.2f}s -> 采样率不对(TTS_RATE={tts.TTS_RATE})"

        beep_path, _ = tts.beep(os.path.join(d, "beep.mulaw"), seconds=0.3)
        assert 0.25 < os.path.getsize(beep_path) / tts.TTS_RATE < 0.35, "提示音时长不对"


if __name__ == "__main__":
    test_agent_has_sys()
    test_vision_server_stop_kills_online_server()
    test_facade_does_not_lie_about_state()
    test_tts_output_is_16k()
    print("ok")
