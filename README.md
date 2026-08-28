# cam-agent — 摄像头 AI 助手

基于大华/Imou 摄像头的 **AI 助手**:语音喊话控制、本地看图、语音播报、多摄像头管理。所有 AI 能力(语音识别 / 意图理解 / 视觉 / TTS)都在本仓库本地运行,通过 **MCP 协议**调用独立的 **mcp-dahua-camera** 仓库控制摄像头设备。

## 架构

```
┌─────────────────────┐         MCP stdio          ┌─────────────────────────┐
│  cam-agent (本仓库)  │ ─────────────────────────> │  mcp-dahua-camera       │
│  语音识别 whisper     │  call: ptz_move/capture/  │  (独立仓库, 纯设备能力)   │
│  意图理解 Qwen3.5     │  search/select/list/...   │  云台/预置点/抓图/麦克风/  │
│  视觉  Qwen3.5       │                           │  扬声器/多摄像头管理      │
│  TTS   edge-tts     │                           │  无任何 AI 依赖          │
└─────────────────────┘                           └─────────────────────────┘
```

- **设备能力归 mcp-dahua-camera**: 云台、预置点、抓图、采集麦克风、播放音频、设备搜索/切换
- **AI 能力归 cam-agent**: 听懂你说话、看懂画面、组织回复、合成语音

## 功能

- **语音对话助手** (`voice`): 一直监听麦克风, 你说"往右转"就转、问"看到什么"就描述, 扬声器回应
- **HTTP 网关** (`gateway`): REST 接口控制摄像头(供 agent 或外部客户端)
- **AI agent** (`agent`): 通过云 API function calling 指挥摄像头
- **本地视觉服务** (`vision-server`): llama-server 进程管理(看图用)
- **多摄像头管理**: 搜索局域网设备、切换摄像头、查看已连接列表

## 快速开始

### 1. 前置

- Python ≥ 3.12, [uv](https://docs.astral.sh/uv/)
- `ffmpeg`(语音播报): `apt install ffmpeg`
- **mcp-dahua-camera** 仓库(独立仓库, 需 clone 到同级目录或用 `MCP_DAHUA_CAMERA_DIR` 指定路径)

### 2. 配置

```bash
cp config.example.ini config.ini   # 填 [ai] 的 api_key / base_url
```

摄像头凭据在 **mcp-dahua-camera** 的 config.ini 配置(默认凭据),运行时通过搜索/切换选择要控制的摄像头。

### 3. 本地模型(语音识别 + 视觉)

```bash
# 语音识别 (faster-whisper small, ~460MB, 缓存在 ~/.cache/huggingface/)
uv run python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"

# 视觉模型 (文本 GGUF ~508MB + 视觉投影 ~198MB)
mkdir -p models
uv run python -c "
from huggingface_hub import snapshot_download
snapshot_download('unsloth/Qwen3.5-0.8B-GGUF', local_dir='models/qwen35-0.8b-unsloth',
                  allow_patterns=['Qwen3.5-0.8B-Q4_K_M.gguf','mmproj-BF16.gguf'])
"
```

> 视觉走 llama.cpp 的 llama-server(原生支持 mmproj), 预编译二进制放 `vendor/llama/`, 或用 `vision-server start` 管理。

### 4. 启动

```bash
uv sync
uv run cam-agent voice          # 语音对话助手
uv run cam-agent gateway        # HTTP 网关 (0.0.0.0:8080)
uv run cam-agent agent          # AI agent (走网关+云API)
uv run cam-agent vision-server start   # 视觉服务
```

## 多摄像头使用

### 语音控制

```
"搜索设备"     → 扫描局域网, 发现所有摄像头
"切换到192.168.10.56" → 切换到指定摄像头
"当前摄像头"   → 查看当前连接的摄像头
"往右转"       → 操控当前摄像头
```

### HTTP 网关

```
GET  /cameras           → 列出已连接的摄像头
GET  /cameras/search    → 搜索局域网设备
POST /cameras/select    → 切换摄像头 {"ip": "192.168.10.56", "password": "..."}
```

## 目录结构

```
cam_agent/
    voice.py         # 语音对话循环 (本地LLM意图理解 + 扬声器回应)
    asr.py           # 语音识别 (faster-whisper)
    vision.py        # 本地视觉 (调用 llama-server)
    vision_server.py # llama-server 进程管理
    tts.py           # 文字转语音 (edge-tts, 带缓存)
    mcp_client.py    # MCP client (连 mcp-dahua-camera, 支持多摄像头)
    gateway.py       # HTTP 网关 (走 MCP client)
    agent.py         # AI agent (走网关 + 云 API function calling)
    config.py        # 配置加载
```

## 4G 内存部署

| 组件 | 内存 |
|------|------|
| faster-whisper small (int8) | ~1GB |
| Qwen3.5-0.8B (视觉+意图) | ~0.7GB |
| 系统 + 摄像头 SDK | <1GB |

> 共约 2-2.5GB, 4G 机器可跑。要更省可换 whisper base (`ASR_MODEL=base`)。
