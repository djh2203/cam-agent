# coding=utf-8
"""cam-agent 命令行入口: python -m cam_agent {gateway|agent}"""
import sys

from .config import load_config


def cmd_gateway(args):
    from .gateway import run_gateway
    from .mcp_client import CameraFacade

    cfg = load_config()
    cam = CameraFacade()
    # MCP server 起不来时 CameraFacade() 已抛异常; 这里只报告是否已选中摄像头
    print(f"[cam-agent] 摄像头: {cam.ip or '未选择(用 POST /cameras/select 指定 IP)'}")
    host = cfg["server"]["host"]
    port = int(cfg["server"]["port"])
    try:
        run_gateway(cam, cfg, host, port)
    finally:
        cam.cleanup()


def cmd_agent(args):
    from .agent import main

    main()


def cmd_voice(args):
    from .voice import main as voice_main

    voice_main()


def cmd_vision_server(args):
    from .vision_server import main as vs_main

    vs_main()


def main():
    if len(sys.argv) < 2:
        print("用法: python -m cam_agent {gateway|agent|voice|vision-server}")
        print("  gateway       启动 HTTP 网关(通过 MCP 调 mcp-dahua-camera)")
        print("  agent         启动 AI 交互助手(需设置 [ai] api_key)")
        print("  voice         启动语音对话助手(本地 LLM 意图理解 + 扬声器回应)")
        print("  vision-server 启动/停止本地视觉服务 llama-server (start|stop|status)")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "gateway":
        cmd_gateway(sys.argv[2:])
    elif cmd == "agent":
        cmd_agent(sys.argv[2:])
    elif cmd == "voice":
        cmd_voice(sys.argv[2:])
    elif cmd == "vision-server":
        cmd_vision_server(sys.argv[2:])
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()