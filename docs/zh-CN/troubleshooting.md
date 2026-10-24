# 排错

先看两个地方：板子串口日志（`python3 tools/mon.py 30 --reset`）和服务端日志（`server/server.log`）。

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 字幕显示「连不上服务器」 | 后台没启动 / Mac 休眠 / Mac IP 变了 | `./start.sh`；关闭 Mac 自动睡眠（见 [Mac 服务](mac-setup.md#让它一直在线)）；路由器给 Mac 做 DHCP 保留，或改 `secrets.h` 的 `CHAT_SERVER` 后重新烧录 |
| `start.sh` 报 LLM server not reachable | LM Studio 没开本地服务或没加载模型 | LM Studio → Developer → Start Server，并加载 `.env` 里的 `LLM_MODEL` |
| 字幕显示「服务器错误 401」 | 板子令牌和服务端不一致 | `secrets.h` 的 `CHAT_TOKEN` 要等于 `server/.env` 的 `VOICE_TOKEN` |
| 左上角 Wi-Fi 红叉 | 5 GHz 网络 / 密码错 | 只支持 2.4 GHz；检查 `secrets.h` |
| 回答很慢（每句 >5 s） | 稠密模型 / 开着思考 / 提示缓存失效 | 换 MoE 模型，或 `LLM_REASONING_EFFORT=none`；`server.log` 里看 `timing_ms` 定位是 asr / llm / tts 哪一步 |
| 开机后第一句特别慢 | 首次 TCP 上传慢 | 固件连上 Wi-Fi 后会自动预热；串口应看到 `[warmup] upload ok` |
| 板子信号很弱（-80 dBm）、偶尔卡住 | Mesh 网络连到了远端 AP | 固件已按信号强度选 AP；串口 `[wifi]` 行会打印 bssid / rssi |
| AUTO 模式喊了没反应 | 没识别到「小奇」/ 声音太小 / 追问窗口已过 | 连喊两遍「小奇小奇」；串口 `v` 看音量是否超过阈值；`server.log` 里 `transcribe ... woken=` 看识别结果 |
| 说了话却被当成噪音 | 音量低于服务端静音门槛 | `server.log` 的 `asr ... rms=`；为空的录音保存在 `server/debug_clips/` |
| 电视 / 旁人说话也触发 | AUTO 没有回声消除，只靠唤醒词过滤 | 正常现象：不含「小奇」的话会被安静忽略；不想要可切回 HOLD |
| 识别出繁体字 / 怪字 | Whisper 自动语种判断偏差 | 已用示例句提示词约束为简体；其他语种结果会按中文重解 |
| 天气 / 股价答错 | 模型没调用专用工具、改用了网页搜索 | `server.log` 里看 `tool ...` 行；关键词路由覆盖不到的问法可在 `voice_server/tools.py` 的 `_ROUTES` 里补 |
| 搜索返回空 | 当前网络下某些引擎被拦截 | `search-eval/eval_search.py` 评测后改 `searxng/settings.yml` 的引擎 |
| 喇叭没声音 | 喇叭没接 / I2S 引脚写反 | 喇叭接 SPEAKER 口；数据线必须 IO8 输出、IO6 输入（见 [hardware.md](hardware.md)） |
| 电量一直显示 93% 左右 | 插着 USB 但没接电池 | 充电芯片把 BAT 撑在约 4.13 V，接上电池后读数正常 |
| 烧录报 `Failed to connect` / 找不到 `/dev/cu.usbmodem*` | 充电线不带数据 / 芯片没进下载模式 | 换数据线；按住 BOOT 按一下 RESET 再松开 BOOT 后重烧，见 [刷机](flashing.md#下载模式) |
| 编译报 PSRAM / 内存错误 | 板型配置不对 | `board_build.arduino.memory_type = qio_opi`（已在 `platformio.ini`） |
