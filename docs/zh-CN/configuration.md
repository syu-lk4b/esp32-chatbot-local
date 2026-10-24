# 配置

## 服务端 `server/.env`

由 `install.sh` 生成（权限 600，已 gitignore），模板见 `server/.env.example`。改完运行 `./start.sh` 重启生效。

| 变量 | 默认 | 说明 |
|---|---|---|
| `VOICE_TOKEN` | 安装时随机生成 | 板子请求头 `X-Token` 必须与之相同（固件 `CHAT_TOKEN`） |
| `VOICE_HOST` / `VOICE_PORT` | `0.0.0.0` / `8765` | 需对局域网开放，板子才能访问 |
| `LLM_BACKEND` | `openai` | `openai`：LM Studio 或任意 OpenAI 兼容服务；`ollama`：Ollama 原生接口 |
| `LLM_URL` | `http://127.0.0.1:1234/v1` | Ollama 用 `http://127.0.0.1:11434` |
| `LLM_MODEL` | `qwen3-30b-a3b-instruct-2507` | 服务端看到的模型 id（LM Studio：`curl $LLM_URL/models`） |
| `LLM_REASONING_EFFORT` | 空 | 推理（thinking）模型设为 `none` 可关闭思考，回复快 2–3 倍 |
| `LLM_MAX_TOKENS` | `600` | 单次回答上限（思考 token 也计入） |
| `USER_LOCATION` | `上海` | 天气、新闻、时间天气卡片的默认地点 |
| `SYSTEM_PROMPT` | 见 `voice_server/config.py` | 可用 `{location}` 占位 |
| `WHISPER_MODEL` | `mlx-community/whisper-large-v3-turbo` | 任意 mlx-whisper 模型 |
| `TTS_ENGINE` | `qwen3` | `qwen3`：自然、带情绪语气（每句约 1 s）；`piper`：快（需 `piper.download_voices` 下载音色） |
| `QWEN_TTS_MODEL` / `QWEN_TTS_VOICE` | `…Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit` / `vivian` | 可选音色：serena, vivian, uncle_fu, ryan, aiden, ono_anna, sohee, eric, dylan |
| `PIPER_VOICE` | `server/models/zh_CN-huayan-medium.onnx` | Piper 音色路径 |
| `SEARXNG_URL` | `http://127.0.0.1:8888` | 本地 SearXNG |
| `DEBUG_CLIPS_DIR` | `server/debug_clips` | 识别为空的录音存这里便于排查；设为空禁用 |

### 换大模型

推荐**响应快的 MoE 模型**（如 Qwen3-30B-A3B Instruct：每个 token 只激活约 3B 参数，闲聊约 0.3 s）。稠密模型（如 27B）回答更细腻但慢几倍；如果是带思考的模型，设 `LLM_REASONING_EFFORT=none`。模型需支持工具调用（function calling）。

### 英文回答

默认系统提示词让小奇用中文回答。想要英文回答：设置 `SYSTEM_PROMPT`（保留 `voice_server/config.py` 默认提示词里关于情绪标签的要求），并换一个英文音色，如 `QWEN_TTS_VOICE=ryan`。唤醒词仍是「小奇小奇」。

### 联网工具

| 工具 | 数据源 | 用途 |
|---|---|---|
| `get_weather` | Open-Meteo（免 key） | 当前天气 + 1–7 天预报 |
| `get_stock_quote` | 腾讯行情（免 key） | A 股 / 港股 / 美股 / 指数 / ETF，名称或代码均可 |
| `get_tech_news` | IT之家、少数派 RSS | 科技新闻，后台每 5 分钟预取 |
| `web_search` | 本地 SearXNG | 其他实时问题 |

SearXNG 启用的引擎在 `searxng/settings.template.yml`（安装时复制为带随机密钥的 `settings.yml`）。默认 Bing / Yandex / Yahoo / Bing 新闻 / DuckDuckGo 新闻，是在中国大陆家庭网络下实测可用的组合；你的网络不同可以用 `search-eval/eval_search.py` 重新评测。

## 固件 `firmware/include/secrets.h`

由 `install.sh` 生成（已 gitignore），模板 `secrets.example.h`：

| 宏 | 说明 |
|---|---|
| `WIFI_SSID` / `WIFI_PASS` | 2.4 GHz Wi-Fi |
| `CHAT_SERVER` | `http://<Mac 的局域网 IP>:8765` |
| `CHAT_TOKEN` | = `server/.env` 的 `VOICE_TOKEN` |

`firmware/platformio.ini` 可加 `-DTZ_INFO=\"<POSIX TZ>\"` 改时钟时区（默认 `CST-8`，即北京时间）；天气卡片显示的城市就是服务端的 `USER_LOCATION`。

`firmware/src/main.cpp` 顶部的常量：

| 常量 | 默认 | 说明 |
|---|---|---|
| `FOLLOW_UP_MS` | 8000 | AUTO 模式回答后免唤醒追问的时间窗 |
| `MAX_RECORD_SECONDS` | 15 | 单句最长录音 |
| `ECHO_HOLDOFF_MS` | 350 | 播完后忽略麦克风的时间，避免听到自己 |
| `SLEEPY_AFTER_MS` | 60000 | 多久没互动后小奇犯困 |
| `EMOTION_HOLD_MS` | 4000 | 说完后保持情绪表情的时长 |
| `WEATHER_REFRESH_MS` | 900000 | 天气卡片刷新间隔（15 分钟） |

AUTO 模式的语音检测阈值在 `firmware/include/vad.h`（`START_FLOOR` / `KEEP_FLOOR` 等），可用串口 `v` 查看实际音量后调整。

## 串口调试命令

用 `python3 tools/mon.py 30` 看日志，或任意串口工具（115200）发送单个字符：

| 命令 | 作用 |
|---|---|
| `t` | 发送 1 秒测试音，走完整链路 |
| `m` | 切换 HOLD / AUTO |
| `k` | 切换造型 |
| `v` | 开关 VAD 音量 / 阈值打印 |
| `b` | 打印电池电压、百分比、是否充电 |
| `c` | 截屏（配合 `tools/snap.py`） |

注意：ESP32-S3 原生 USB 下打开串口会复位芯片，发命令前等启动完成（约 8–10 秒，`snap.py --wait 10`）。
