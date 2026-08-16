# coding=utf-8
"""camgate 命令行入口: python -m camgate {gateway|agent}"""
import sys

from .config import load_config


def cmd_gateway(args):
    from .camera import Camera
    from .gateway import run_gateway

    cfg = load_config()
    cam = Camera(
        cfg["camera"]["ip"],
        cfg["camera"]["port"],
        cfg["camera"]["username"],
        cfg["camera"]["password"],
        cfg["camera"]["channel"],
    )
    cam.login()
    print(f"[camgate] 摄像头登录成功: {cfg['camera']['ip']}")
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


def cmd_mcp(args):
    from .mcp_server import main as mcp_main

    mcp_main()


def cmd_vision_server(args):
    from .vision_server import main as vs_main

    vs_main()


def main():
    if len(sys.argv) < 2:
        print("用法: python -m camgate {gateway|agent|voice|mcp|vision-server}")
        print("  gateway       启动 HTTP 网关(需先配置 config.ini)")
        print("  agent         启动 AI 交互助手(需设置 [ai] api_key)")
        print("  voice         启动语音对话助手(监听麦克风+云 LLM+扬声器回应)")
        print("  mcp           启动 MCP server(stdin/stdout 协议, 供支持 MCP 的 agent 接入)")
        print("  vision-server 启动/停止本地视觉服务 llama-server (start|stop|status)")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "gateway":
        cmd_gateway(sys.argv[2:])
    elif cmd == "agent":
        cmd_agent(sys.argv[2:])
    elif cmd == "voice":
        cmd_voice(sys.argv[2:])
    elif cmd == "mcp":
        cmd_mcp(sys.argv[2:])
    elif cmd == "vision-server":
        cmd_vision_server(sys.argv[2:])
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()