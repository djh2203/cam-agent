# AGENTS.md — cam-agent

AI 助手仓库:语音/视觉/意图理解全本地(faster-whisper + Qwen3.5-0.8B 经 llama-server),通过 **MCP 协议**调用兄弟仓库 `mcp-dahua-camera` 控制摄像头设备。本仓库**不直接碰摄像头 SDK**。

## 架构要点

```
cam-agent (本仓库)  --MCP stdio-->  mcp-dahua-camera (同级目录, 独立 git 仓库)
  本地AI: whisper/Qwen/TTS              纯设备: 云台/预置点/抓图/麦克风/扬声器
```

- 设备调用统一走 `cam_agent/mcp_client.py`:`MCPCameraClient`(后台 asyncio 线程持 stdio 会话)+ `CameraFacade`(给 gateway 用的壳)+ `get_client()` 单例。
- **voice 和 agent 都直接 `get_client()`,不走 HTTP**;`gateway` 是给外部客户端的 REST 门面,不是内部调用链的一环。三条链路各自起一个 MCP server 子进程,所以**不要同时跑 voice 和 agent**(会各自登录摄像头、抢对讲通道)。
- MCP 仓库路径:环境变量 `MCP_DAHUA_CAMERA_DIR` 覆盖,默认 `<cam-agent 上级>/mcp-dahua-camera`。**找不到该仓库则 gateway/voice/agent 全部起不来**。
- **摄像头凭据不在这里**——默认凭据在 mcp-dahua-camera 的 `config.ini`,每台设备的凭据在该仓库的 `cameras.json`。本仓库 `config.ini` 只含 [server]/[ai]/[tts]。
- 不变量:`CameraFacade` 未选摄像头时必须 `online=False` / `loginID=0` / `ip=None`,选中后才置真。gateway 的 `/health`、`/info`(401)、启动提示都依赖它——别为了"看起来正常"把初值改成 True。

## 命令

```bash
uv run cam-agent voice          # 语音对话助手(主入口)
uv run cam-agent gateway        # HTTP 网关 (默认 127.0.0.1:8080, ThreadingHTTPServer)
uv run cam-agent agent          # 交互式 AI agent (命令行, 本地 llama-server function calling)
uv run cam-agent vision-server start|stop|status [port]   # llama-server 生命周期
```

- 入口在 `cam_agent/__main__.py`;包目录叫 `cam_agent`,pyproject 名字是 `cam-agent`。
- `voice`:本地 llama-server 意图理解,function calling 精简为 6 工具(search_devices/select_camera/list_cameras/ptz_move/preset/analyze_image),固定录 6 秒。`agent`:11 工具,走 gateway REST + 同一个本地 llama-server。**两者都不读 [ai]**,`[ai]` 只在 `[tts] backend = openai` 时用。
- 测试:`python3 tests/test_smoke.py`(无硬件自检:facade 状态如实 + `vision-server stop` 真停 + agent 自动拉起 gateway 用得到的 `sys` 已导入)。设备行为仍需真机跑 `voice`/`gateway` 冒烟。

## gateway 路由(外部客户端)

`GET /health /info /frame.jpg /cameras /cameras/search` · `POST /ptz /ptz/stop /preset /capture /speak /beep /cameras/select`

没有 `/login` `/logout`:会话由 MCP server 常驻持有,选设备就是 `POST /cameras/select {"ip": "..."}`。

## 本地模型/服务(4G 无 GPU 服务器)

- 意图理解+视觉共用 `llama-server`(OpenAI 兼容,默认 `http://127.0.0.1:8081/v1`,`VISION_BASE_URL` 可覆盖)。`vision.analyze_image` 在 URLError 时会自动 `ensure_running()` 拉起 llama-server。
- llama-server 二进制 `vendor/llama/llama-b9543/llama-server`(gitignored),启动需 `LD_LIBRARY_PATH` 指向其目录;模型 `models/qwen35-0.8b-unsloth/`(Q4_K_M.gguf + mmproj-BF16.gguf,gitignored)。
- `vision-server stop` 按端口 `fuser -k`(不看 PID 文件,服务在线离线都能停)。
- ASR:`faster-whisper small` CPU int8,`asr.py` 强制 `HF_HUB_OFFLINE=1`(离线),`ASR_MODEL=base` 可省内存。whisper 模型若未缓存会卡住,先按 README 下载。
- TTS:edge-tts,`tts.py` 有缓存(`media/.tts_cache/`,同文本秒回)。
- 启动前确认 llama-server 在线:`uv run cam-agent vision-server status`。

## 语音链路的设备特性(踩过的坑)

- 摄像头麦克风**静音也回传数据块**,无法靠数据量/能量判断说话结束,`voice.py` 固定录 6 秒(用户已拍板,别改成"自动结束")。
- 对讲通道被占会重试,`record_audio` 最长重试 120 秒——和 `MCPCameraClient.call()` 的 120s 超时相等,极端情况下客户端会先抛 `TimeoutError` 而不是干净的错误信息。要调就一起调。
- 该分支里的"是否重启摄像头"询问只在终端(`sys.stdin.isatty()`)才弹;经 MCP stdio 调用时 stdin 是协议管道,弹了会挂起并污染协议,所以被挡掉了。
- 静音抑制下大声短促喊话效果最好;whisper `beam_size=1` + `vad_filter` 已调好,别乱动。
- `analyze_image` = A 仓库 `capture` + 本地视觉;大华/IMOU 都能用,IMOU 麦克风采样率 16kHz。

## 配置(本仓库 config.ini,gitignored)

`[server]` host/port(gateway 监听)、`[ai]` 仅 openai TTS 用、`[tts]` backend=edge。环境变量覆盖:`GATE_HOST`/`GATE_PORT`/`AI_*`/`TTS_*`/`VISION_BASE_URL`/`ASR_MODEL`。不要把真实 api_key 提交。

## 多摄像头使用

- `select_camera(ip)` 只需传 IP,密码已保存在 A 仓库的 `cameras.json`(gitignored,首次连接成功后自动写入)
- 首次连接需提供密码: `select_camera(ip, password=xxx)`
- `list_cameras` 返回所有已连接+已保存的摄像头
- `search_devices` 扫描局域网, 发现新设备(`agent` 模式下这个工具叫 `search_cameras`,走 gateway `/cameras/search`)
