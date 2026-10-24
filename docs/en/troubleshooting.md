# Troubleshooting

Look in two places first: the board's serial log (`python3 tools/mon.py 30 --reset`) and the server log (`server/server.log`).

| Symptom | Likely cause | Fix |
|---|---|---|
| Caption says "连不上服务器" (can't reach server) | backend not running / Mac asleep / Mac IP changed | `./start.sh`; disable Mac sleep (see [Mac setup](mac-setup.md#keeping-it-always-on)); reserve the Mac's IP in DHCP, or update `CHAT_SERVER` in `secrets.h` and reflash |
| `start.sh` says LLM server not reachable | LM Studio's server isn't started or no model is loaded | LM Studio → Developer → Start Server, and load the `LLM_MODEL` from `.env` |
| Caption says "服务器错误 401" (server error 401) | board token doesn't match the server | `CHAT_TOKEN` in `secrets.h` must equal `VOICE_TOKEN` in `server/.env` |
| Red cross on the Wi-Fi icon | 5 GHz network / wrong password | 2.4 GHz only; check `secrets.h` |
| Slow replies (>5 s each) | dense model / thinking on / prompt cache missing | use an MoE model or `LLM_REASONING_EFFORT=none`; `timing_ms` in `server.log` shows whether asr / llm / tts is slow |
| First reply after boot is very slow | first TCP upload is slow | the firmware warms up after joining Wi-Fi; the serial log should show `[warmup] upload ok` |
| Weak signal (-80 dBm), occasional stalls | mesh network joined a distant AP | the firmware picks the strongest AP; the `[wifi]` serial line prints bssid / rssi |
| AUTO mode ignores the wake word | "小奇" not recognized / too quiet / follow-up window over | say "小奇小奇" twice; check levels with serial `v`; look for `transcribe ... woken=` in `server.log` |
| Speech treated as noise | below the server's silence gate | `asr ... rms=` in `server.log`; empty recordings are saved in `server/debug_clips/` |
| TV / other people trigger it | AUTO has no echo cancellation and relies on the wake word | expected: speech without "小奇" is silently ignored; switch to HOLD if it bothers you |
| Traditional or odd characters | Whisper language detection drift | the example-sentence prompt keeps it to Simplified Chinese; other languages are re-decoded as Chinese |
| Wrong weather / stock numbers | the model skipped the dedicated tool and used web search | check `tool ...` lines in `server.log`; add phrasings the keyword routes miss to `_ROUTES` in `voice_server/tools.py` |
| Search returns nothing | some engines are blocked on your network | evaluate with `search-eval/eval_search.py` and change the engines in `searxng/settings.yml` |
| No sound | speaker not connected / I2S pins swapped | speaker on SPEAKER; data must be IO8 out, IO6 in (see [Hardware](hardware.md)) |
| Battery stuck around 93% | USB plugged in, no battery | the charger holds BAT at ~4.13 V; readings are correct with a battery attached |
| Upload fails with `Failed to connect` / no `/dev/cu.usbmodem*` | charge-only cable / chip not in download mode | use a data cable; hold BOOT, tap RESET, release BOOT, then flash again (see [Flashing](flashing.md#download-mode)) |
| Build error about PSRAM / memory | wrong board config | `board_build.arduino.memory_type = qio_opi` (already in `platformio.ini`) |
