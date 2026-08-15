# camgate — 大华摄像头 MCP 网关

把大华网络摄像头变成标准的 **MCP Server**,让任何支持 MCP 的 AI 客户端(Claude Desktop / Cursor / opencode / Cherry Studio 等)都能直接控制摄像头:云台旋转、绝对定位、预置点、抓图、语音播报,还能**语音识别喊话**、**本地视觉看图**(Qwen3.5-0.8B)。

## 功能

MCP 工具(共 11 个):

| 工具 | 说明 |
|------|------|
| `camera_status` | 摄像头在线状态 |
| `camera_info` | 摄像头信息(序列号/通道/类型) |
| `ptz_move` | 云台方向移动(上下左右/对角), speed 1-255, 满速 255 最明显 |
| `ptz_stop` | 停止云台移动 |
| `ptz_abs_move` | **绝对坐标定位**(仅水平角, 0/90/180/270 精准) |
| `ptz_get_position` | 读取云台角度(该设备返回值不可靠, 仅供参考) |
| `preset` | 预置点 set/goto/del |
| `capture` | 抓取一帧画面保存到 media/ |
| `speak` | 文字 TTS 通过摄像头扬声器播报(中文, G711 mu-law) |
| `listen` | 采集摄像头麦克风并语音识别为文字(faster-whisper, 本地) |
| `analyze_image` | 抓图并用本地视觉模型分析画面内容(内置 Qwen3.5-0.8B) |

## 快速开始

### 1. 依赖

- Python ≥ 3.12, [uv](https://docs.astral.sh/uv/)
- `ffmpeg`(语音播报用): `apt install ffmpeg`
- 大华官方 Python NetSDK(wheel 已内置在 `vendor/`)

### 本地语音识别 + 视觉模型(可选)

`listen`(whisper)和 `analyze_image`(Qwen3.5-0.8B)需要本地模型,首次会自动从 HuggingFace 下载:

```bash
# 语音识别模型 (faster-whisper small, ~460MB, 缓存在 ~/.cache/huggingface/)
uv run python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"

# 视觉模型 (文本 GGUF ~508MB + 视觉投影 ~198MB, 放到 models/qwen35-0.8b-unsloth/)
mkdir -p models
uv run python -c "
from huggingface_hub import snapshot_download
snapshot_download('unsloth/Qwen3.5-0.8B-GGUF', local_dir='models/qwen35-0.8b-unsloth',
                  allow_patterns=['Qwen3.5-0.8B-Q4_K_M.gguf','mmproj-BF16.gguf'])
"
```

> 国内网络可先 `export HF_ENDPOINT=https://hf-mirror.com`。也可以设 `VISION_TEXT_GGUF` / `VISION_MMPROJ` 环境变量指向自己的模型文件。

### 2. 安装

```bash
git clone <repo-url> camgate
cd camgate
cp config.example.ini config.ini   # 填入你的摄像头 IP/密码
uv sync
```

### 3. 配置摄像头

编辑 `config.ini`:

```ini
[camera]
ip = 192.168.1.10      # 摄像头局域网 IP
port = 37777
username = admin
password = change_me
```

### 4. 接入你的 AI 客户端

在支持 MCP 的客户端里添加一个本地 stdio MCP server:

```json
{
  "mcp": {
    "camgate": {
      "type": "local",
      "command": ["uv", "run", "--project", "/path/to/camgate", "camgate", "mcp"]
    }
  }
}
```

Claude Desktop(`claude_desktop_config.json`)、Cursor、opencode 等配置方式类似。

### 5. 命令行验证

```bash
uv run camgate mcp       # 启动 MCP server (stdio)
uv run camgate gateway   # 可选: 启动 HTTP 网关 (127.0.0.1:8080)
```

## 语音播报说明

- 默认用 `edge-tts`(免费、中文效果好),需联网。
- 也可以改用 OpenAI 兼容 `/audio/speech` 接口,在 `config.ini [ai]` 填 `backend = openai` 和 `api_key`。
- 音频以 G711 mu-law 编码推送到摄像头扬声器。

## 设备能力与已知限制

以下结论基于 DH-SD1 协议球机(如 DH-IPC-H4AC)实测,不同型号可能不同,可用 `scripts/query_caps.py` 查询:

- **水平绝对定位精准**: `ptz_abs_move` 只对水平角生效, 0=正面 / 90=左 / 180=背面 / 270=右, 误差极小。
- **垂直(tilt)只能用相对移动**: 该协议绝对定位不生效于垂直轴, 上下调节请用 `ptz_move("up"/"down")`, speed 建议 255(满速约 35°/秒), 按 `seconds` 控制幅度。
- **读位置不可靠**: `ptz_get_position` 返回设备内部导航坐标(与物理方向不一致), 仅作参考, 不能依赖它做闭环控制。
- **人形/移动侦测**: 支持智能动检(`SmartMotionDetect`), 可订阅人形事件并收到抓拍图; 但该型号**不返回人形框坐标**(事件结构体中对象数为 0)。如需框坐标, 需换带 AI 的型号或本地 YOLO。
- **无麦克风采集**: `StartTalkEx`/`RecordStartEx` 在该设备返回失败, 不支持把摄像头麦克风音频上行到 PC(只能播放, 不能录制)。
- **无变倍(Zoom)**: 云台只有 Pan/Tilt, `zoomin/zoomout` 无效。

### 麦克风采集

该设备**支持上行采集音频**(对讲通道空闲时):`listen` 工具用 `StartTalkByDataType` 采到 **8000Hz 16bit PCM**,交给本地 faster-whisper 识别。

注意:
- 若报错误 `talk has opened by other client`(对讲通道被占用),**重启摄像头**即可恢复(演示 `scripts/test_mic.py`)。
- 设备有静音抑制,录音时**大声、持续**说话效果更好;静音片段不会被回传,识别出的文字可能略有重复。

### 语音/视觉控制闭环

喊话控制云台的链路已经全部打通:

```
对摄像头喊话 → listen(whisper 识别成文字) → LLM 解析意图 → ptz_move/ptz_abs_move 执行
抓一帧画面 → analyze_image(本地 Qwen3.5-0.8B 看图) → 画面内容
```

### 语音对话助手(对讲机模式)

命令行启动后**一直监听**摄像头,识别到你说话就交给云 LLM 理解并控制摄像头,回复通过扬声器播报:

```bash
uv run camgate voice
```

```
你喊"往右转" → whisper 识别(本地) → 云 LLM(function calling) → ptz_move 执行 → 扬声器回应
```

- 意图理解用 `config.ini [ai]` 的 OpenAI 兼容接口(可填 `chat_model = auto` 自动选模型)。
- `listen`(听)、`analyze_image`(本地看图)都在本地,只有 LLM 决策走云端。

## 弱服务器部署(4G 内存、无 GPU)

所有识别都在本地 CPU 推理,适合低配机器:

| 组件 | 内存占用 |
|------|---------|
| faster-whisper `small` (int8) | ~1GB |
| Qwen3.5-0.8B Q4_K_M + mmproj | ~0.7GB(加载后常驻) |
| 系统 + 摄像头 SDK | <1GB |

> 共约 2-2.5GB,4G 内存机器可跑。若要更省,whisper 可换 `base`(环境变量 `ASR_MODEL`),视觉可用 Q3 量化。

## 目录结构

```
camgate/
  camgate/
    __main__.py    # CLI 入口 (gateway/agent/mcp)
    config.py      # 配置加载
    camera.py      # NetSDK 封装 (登录/云台/抓图/对讲/绝对定位/录音)
    gateway.py     # HTTP 网关
    tts.py         # TTS 转 G711 音频
    asr.py         # 语音识别 (faster-whisper)
    vision.py      # 本地视觉理解 (Qwen3.5-0.8B)
    mcp_server.py  # MCP server (核心)
    agent.py       # 可选: AI 交互助手
  scripts/
    search_devices.py  # 局域网搜索摄像头
    query_caps.py      # 查询设备能力集
    test_ptz.py        # 云台自测(从 config.ini 读凭据)
  vendor/          # 内置的大华 NetSDK wheel
  config.example.ini
```

## 免责声明

- 摄像头凭据请保管好,`config.ini` 已加入 `.gitignore`。
- 设备具体能力(是否支持绝对定位/预置点删除)取决于固件,`scripts/query_caps.py` 可查询。
- 本项目仅用于个人对自有设备的控制,请遵守相关法律法规。