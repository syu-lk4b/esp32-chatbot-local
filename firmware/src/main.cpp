// 小奇 · husky voice chat for LCDWIKI ES3C28P.
//
// Skins (paw button beside the husky cycles): chibi, pixel, cyber.
// Husky frames come from data/skins.bin (tools/gen_skins.py); the face follows
// the conversation state and the reply's emotion tag.
//
// HOLD mode: hold the screen to talk, release to send.
// AUTO mode: always listening; an energy VAD cuts utterances, and only ones that start with
// the wake word "小奇小奇" get answered (plus follow-ups within FOLLOW_UP_MS of a reply).
// Either way the clip goes to the LAN voice server (Whisper -> local LLM with
// web search -> Piper), which replies with text + speech. Tap to interrupt.
//
// Threads: loop() on core 1 owns display, touch and microphone;
// chatTask on core 0 does HTTP and speaker playback.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <time.h>

#include <vector>

#include "audio.h"
#include "battery.h"
#include "lgfx_es3c28p.h"
#include "secrets.h"
#include "skin_frames.h"
#include "vad.h"

#define LOGE(fmt, ...) Serial.printf("[err] " fmt "\n", ##__VA_ARGS__)

constexpr int SCREEN_W = 320;
constexpr int SCREEN_H = 240;
constexpr float MAX_RECORD_SECONDS = 15.0f;
constexpr uint32_t MIN_HOLD_MS = 300;
constexpr uint32_t CHAT_TIMEOUT_MS = 60000;
constexpr size_t MAX_REPLY_AUDIO_BYTES = 30 * audio::SAMPLE_RATE * 2;  // 30 s
constexpr uint32_t ERROR_SHOW_MS = 4000;
constexpr uint32_t FRAME_MS = 50;
constexpr uint32_t ECHO_HOLDOFF_MS = 350;  // ignore the mic right after our own speech
constexpr int PREROLL_CHUNKS = 20;         // ~320 ms kept from before the VAD opens
constexpr uint32_t FOLLOW_UP_MS = 8000;    // AUTO: after a reply, follow-ups need no wake word
constexpr uint32_t WEATHER_REFRESH_MS = 15 * 60 * 1000;
constexpr uint32_t WEATHER_RETRY_MS = 60 * 1000;
#ifndef TZ_INFO
#define TZ_INFO "CST-8"  // POSIX TZ for the clock card; override with -DTZ_INFO=\"...\"
#endif

// Layout
constexpr int BG_CX = 160, BG_CY = 100;  // background gradient centre (mirrored in tools/gen_skins.py)
constexpr int TOGGLE_X = 196, TOGGLE_Y = 4, TOGGLE_W = 92, TOGGLE_H = 18;
constexpr int RESET_X = 292;  // reset hit area: x >= RESET_X, y < TOP_HIT_H
constexpr int SKIN_BTN_X = 258, SKIN_BTN_Y = 72, SKIN_BTN_R = 13;  // paw button right of the husky
constexpr int SKIN_BTN_HIT = 24;  // touch radius, generous for fingers
constexpr int TOP_HIT_H = 30;
constexpr int STATE_Y = 134, YOU_Y = 150, AI_Y = 172, LINE_H = 19, AI_MAX_LINES = 3;
constexpr int TEXT_W = 292;

// Palette (RGB565)
constexpr uint16_t C_BG_CENTER = 0x1845;  // #1a0b2e
constexpr uint16_t C_BG_EDGE = 0x0001;    // #05030a
constexpr uint16_t C_PURPLE = 0xBB3F;     // #b967ff
constexpr uint16_t C_PURPLE_DIM = 0x6A51;
constexpr uint16_t C_GREEN = 0x07F4;   // #05ffa1
constexpr uint16_t C_YELLOW = 0xFEA1;  // #ffd60a
constexpr uint16_t C_CYAN = 0x079F;    // #00f0ff
constexpr uint16_t C_RED = 0xF98A;
constexpr uint16_t C_TEXT = TFT_WHITE;
constexpr uint16_t C_SUB = 0x8C73;
constexpr uint16_t C_PINK = 0xFCF6;

// Character animation timing
constexpr uint32_t BLINK_MS = 140;
constexpr uint32_t SLEEPY_AFTER_MS = 60000;
constexpr uint32_t EMOTION_HOLD_MS = 4000;  // keep the reply's face this long after speaking
constexpr int TALK_LEVEL = 4000;            // playback peak above which the mouth opens
constexpr float LISTEN_WAVE_LEVEL = 60.0f;  // mic rms above which the ear waves grow

extern const uint8_t skins_bin_start[] asm("_binary_data_skins_bin_start");

static const char* const WEEKDAYS[] = {"周日", "周一", "周二", "周三", "周四", "周五", "周六"};

static LGFX lcd;
static LGFX_Sprite canvas(&lcd);
static LGFX_Sprite background(&lcd);
static Preferences prefs;

// ---------------------------------------------------------------- shared state

enum class Phase : uint8_t { Idle, Listening, Thinking, Speaking };
enum class Mode : uint8_t { Hold, Auto };
// Same order as the frame atlas in data/skins.bin.
enum class Skin : uint8_t { Chibi, Pixel, Cyber, Count };

static SemaphoreHandle_t g_mtx;
static volatile Phase g_phase = Phase::Idle;
static volatile Mode g_mode = Mode::Hold;
static String g_lastUser, g_lastAi, g_error;  // guarded by g_mtx
static String g_emotion = "calm";             // guarded by g_mtx
static volatile uint32_t g_emotionUntil = 0;
static volatile Skin g_skin = Skin::Chibi;
static volatile uint32_t g_lastActivity = 0;
static volatile uint32_t g_followUntil = 0;   // AUTO follow-up window end (millis)
static volatile uint32_t g_recStartedAt = 0;  // when the current utterance began
static volatile bool g_wakeCheck = false;      // waiting on /api/transcribe

// Clock card weather, from the server's /api/weather (guarded by g_mtx).
struct WeatherNow {
  bool valid = false;
  int temp = 0, tmin = 0, tmax = 0;
  String text, icon;
};
static WeatherNow g_weather;
static uint32_t g_errorAt = 0;
static volatile bool g_abortPlayback = false;
static volatile bool g_resetRequested = false;
static volatile bool g_warmupRequested = false;
static volatile bool g_storyRequested = false;  // serial 's': long reply without speaking
static audio::PlayStats g_play;
// Share of the reply text covered before / by the chunk now playing, for subtitle scrolling.
static volatile float g_chunkFrom = 0, g_chunkSpan = 1;

static int16_t* g_rec = nullptr;  // PSRAM recording buffer
static const size_t REC_CAPACITY = (size_t)(MAX_RECORD_SECONDS * audio::SAMPLE_RATE);
static volatile size_t g_recLen = 0;
static TaskHandle_t g_chatTask = nullptr;

static void setExchange(const String& user, const String& ai, const String& emotion = "calm") {
  xSemaphoreTake(g_mtx, portMAX_DELAY);
  g_lastUser = user;
  g_lastAi = ai;
  g_emotion = emotion;
  xSemaphoreGive(g_mtx);
}

static void setError(const String& e) {
  LOGE("%s", e.c_str());
  xSemaphoreTake(g_mtx, portMAX_DELAY);
  g_error = e;
  g_errorAt = millis();
  xSemaphoreGive(g_mtx);
}

// ---------------------------------------------------------------- network

static void addCommonHeaders(HTTPClient& http) {
  http.addHeader("X-Token", CHAT_TOKEN);
  http.addHeader("X-Device-Id", WiFi.macAddress());
}

// Downloads reply speech into PSRAM. Caller frees. Returns sample count via n.
static int16_t* fetchAudio(const String& path, size_t& n) {
  n = 0;
  HTTPClient http;
  WiFiClient client;
  if (!http.begin(client, String(CHAT_SERVER) + path)) return nullptr;
  http.setTimeout(CHAT_TIMEOUT_MS);
  addCommonHeaders(http);
  int status = http.GET();
  int size = http.getSize();
  if (status != HTTP_CODE_OK || size <= 0 || (size_t)size > MAX_REPLY_AUDIO_BYTES) {
    LOGE("audio GET -> %d size=%d", status, size);
    http.end();
    return nullptr;
  }
  auto* buf = static_cast<uint8_t*>(ps_malloc(size));
  if (!buf) {
    http.end();
    return nullptr;
  }
  WiFiClient* stream = http.getStreamPtr();
  size_t got = 0;
  uint32_t deadline = millis() + CHAT_TIMEOUT_MS;
  while (got < (size_t)size && millis() < deadline && (stream->connected() || stream->available())) {
    int r = stream->read(buf + got, size - got);
    if (r > 0) {
      got += r;
    } else {
      delay(2);
    }
  }
  http.end();
  if (got < (size_t)size) {
    LOGE("audio truncated %u/%d", got, size);
    free(buf);
    return nullptr;
  }
  n = got / 2;
  return reinterpret_cast<int16_t*>(buf);
}

static void doReset() {
  HTTPClient http;
  WiFiClient client;
  if (http.begin(client, String(CHAT_SERVER) + "/api/reset")) {
    addCommonHeaders(http);
    int status = http.POST("");
    if (status != HTTP_CODE_OK) LOGE("reset -> %d", status);
    http.end();
  }
  setExchange("", "");
}

// POSTs a body and parses the JSON reply; reports failures on screen.
static bool postForJson(const char* path, const uint8_t* body, size_t len, const char* contentType,
                        bool handsFree, JsonDocument& doc) {
  HTTPClient http;
  WiFiClient client;
  if (!http.begin(client, String(CHAT_SERVER) + path)) {
    setError("服务器地址无效");
    return false;
  }
  http.setTimeout(CHAT_TIMEOUT_MS);
  addCommonHeaders(http);
  http.addHeader("Content-Type", contentType);
  if (handsFree) http.addHeader("X-Hands-Free", "1");
  int status = http.POST(const_cast<uint8_t*>(body), len);
  if (status != HTTP_CODE_OK) {
    http.end();
    setError(status < 0 ? "连不上服务器" : "服务器错误 " + String(status));
    return false;
  }
  DeserializationError err = deserializeJson(doc, http.getString());
  http.end();
  if (err) {
    setError("回复解析失败");
    return false;
  }
  return true;
}

// Reply speech comes as ordered sentence chunks. While one plays, this task downloads the
// next ones into a short queue, so sentences follow each other without a download gap.
// It ends the queue with a null clip (also on a failed download), then deletes itself.
struct Clip {
  int16_t* pcm;
  size_t n;
};
constexpr int MAX_REPLY_CHUNKS = 16;
constexpr int PREFETCH_DEPTH = 2;

struct Prefetch {
  String urls[MAX_REPLY_CHUNKS];
  int count = 0;
  QueueHandle_t queue = nullptr;
};

static void prefetchTask(void* arg) {
  auto* job = static_cast<Prefetch*>(arg);
  for (int i = 0; i < job->count && !g_abortPlayback; i++) {
    Clip clip{nullptr, 0};
    clip.pcm = fetchAudio(job->urls[i], clip.n);
    if (!clip.pcm) break;
    xQueueSend(job->queue, &clip, portMAX_DELAY);
  }
  Clip end{nullptr, 0};
  xQueueSend(job->queue, &end, portMAX_DELAY);
  vTaskDelete(nullptr);
}

// Shows and speaks a chat reply ({user, reply, emotion, audio_urls, audio_chars}; older
// servers send a single audio_url).
static void playReply(JsonDocument& doc, uint32_t t0) {
  String user = doc["user"] | "";
  String reply = doc["reply"] | "";
  String emotion = doc["emotion"] | "calm";
  Serial.printf("[chat] user=%s reply=%s emotion=%s rtt=%lums\n", user.c_str(), reply.c_str(), emotion.c_str(),
                millis() - t0);
  if (reply.isEmpty()) return;  // hands-free noise: stay quiet
  setExchange(user, reply, emotion);

  auto* job = new Prefetch();
  int chars[MAX_REPLY_CHUNKS] = {};
  int totalChars = 0;
  JsonArrayConst urls = doc["audio_urls"];
  JsonArrayConst sizes = doc["audio_chars"];
  for (size_t i = 0; i < urls.size() && job->count < MAX_REPLY_CHUNKS; i++) {
    chars[job->count] = max(1, sizes[i] | 1);
    totalChars += chars[job->count];
    job->urls[job->count++] = urls[i] | "";
  }
  if (job->count == 0 && (doc["audio_url"] | "")[0]) {
    job->urls[job->count++] = doc["audio_url"] | "";
    chars[0] = totalChars = 1;
  }
  if (job->count == 0) {
    delete job;
    setError("语音下载失败");
    return;
  }

  g_abortPlayback = false;
  job->queue = xQueueCreate(PREFETCH_DEPTH, sizeof(Clip));
  if (!job->queue || xTaskCreatePinnedToCore(prefetchTask, "prefetch", 12 * 1024, job, 2, nullptr, 0) != pdPASS) {
    if (job->queue) vQueueDelete(job->queue);
    delete job;
    setError("内存不足");
    return;
  }

  // Play clips as they arrive; after an abort keep draining (and freeing) until the end marker.
  int played = 0, doneChars = 0;
  uint32_t waitedMs = 0;  // time the speaker sat idle waiting for a later chunk's download
  Clip clip;
  for (;;) {
    uint32_t w = millis();
    if (xQueueReceive(job->queue, &clip, portMAX_DELAY) != pdTRUE || !clip.pcm) break;
    if (!g_abortPlayback) {
      if (played == 0) {
        Serial.printf("[chat] first audio %.1fs ready after %lums (%d chunks)\n", clip.n / (float)audio::SAMPLE_RATE,
                      millis() - t0, job->count);
        g_phase = Phase::Speaking;
        audio::speakerOn();
      } else {
        waitedMs += millis() - w;
      }
      g_chunkFrom = (float)doneChars / totalChars;
      g_chunkSpan = (float)chars[played] / totalChars;
      g_play.progress = 0;
      audio::play(clip.pcm, clip.n, &g_abortPlayback, &g_play, false);
    }
    doneChars += chars[played++];
    free(clip.pcm);
  }
  if (played > 0) audio::speakerOff();
  if (played > 1) Serial.printf("[chat] %d chunks played, waited %lums for downloads\n", played, waitedMs);
  vQueueDelete(job->queue);
  int expected = job->count;
  delete job;  // the task sent the end marker as its last act, so it no longer touches job

  if (played == 0) {
    setError("语音下载失败");
    return;
  }
  if (played < expected && !g_abortPlayback) LOGE("reply audio cut short: %d/%d chunks", played, expected);
  g_chunkFrom = 0;
  g_chunkSpan = 1;
  g_play.progress = 1;
  g_emotionUntil = millis() + EMOTION_HOLD_MS;
  // In AUTO mode a reply opens a short window where follow-ups need no wake word.
  if (g_mode == Mode::Auto) g_followUntil = millis() + FOLLOW_UP_MS;
}

static void chatText(const String& text, uint32_t t0) {
  JsonDocument req;
  req["text"] = text;
  String body;
  serializeJson(req, body);
  JsonDocument reply;
  if (postForJson("/api/chat_text", reinterpret_cast<const uint8_t*>(body.c_str()), body.length(),
                  "application/json", false, reply)) {
    playReply(reply, t0);
  }
}

static void doChat(size_t samples, bool handsFree, bool requireWake) {
  uint32_t t0 = millis();
  const auto* pcm = reinterpret_cast<const uint8_t*>(g_rec);
  JsonDocument doc;
  if (!requireWake) {
    if (postForJson("/api/chat", pcm, samples * 2, "application/octet-stream", handsFree, doc)) playReply(doc, t0);
    return;
  }

  // Wake-word mode: a cheap transcription first decides whether we were addressed.
  // The husky keeps its listening face meanwhile, so ignored chatter never looks like "thinking".
  g_wakeCheck = true;
  bool ok = postForJson("/api/transcribe", pcm, samples * 2, "application/octet-stream", true, doc);
  g_wakeCheck = false;
  if (!ok) return;
  bool woken = doc["woken"] | false;
  String rest = doc["rest"] | "";
  Serial.printf("[wake] text=%s woken=%d rtt=%lums\n", (doc["text"] | ""), woken, millis() - t0);
  if (!woken) return;

  chatText(rest, t0);
}

// The first audio upload after boot takes 2-4 s (later ones ~0.5 s), so push half a
// second of silence through /api/transcribe once Wi-Fi is up; the server skips ASR
// for silence, so this costs it nothing.
static void doWarmup() {
  uint32_t t0 = millis();
  const size_t n = audio::SAMPLE_RATE / 2;
  auto* silence = static_cast<uint8_t*>(ps_calloc(n, sizeof(int16_t)));
  if (!silence) return;
  JsonDocument doc;
  bool ok = postForJson("/api/transcribe", silence, n * sizeof(int16_t), "application/octet-stream", true, doc);
  free(silence);
  Serial.printf("[warmup] upload %s in %lums\n", ok ? "ok" : "failed", millis() - t0);
}

// Polls the server for the clock card's weather; independent of chats.
static void weatherTask(void*) {
  for (;;) {
    if (!WiFi.isConnected()) {
      vTaskDelay(pdMS_TO_TICKS(2000));  // boot: fetch as soon as Wi-Fi comes up
      continue;
    }
    uint32_t wait = WEATHER_RETRY_MS;
    {
      HTTPClient http;
      WiFiClient client;
      if (http.begin(client, String(CHAT_SERVER) + "/api/weather")) {
        http.setTimeout(CHAT_TIMEOUT_MS);
        addCommonHeaders(http);
        int status = http.GET();
        JsonDocument doc;
        if (status == HTTP_CODE_OK && !deserializeJson(doc, http.getString())) {
          WeatherNow w;
          w.valid = true;
          w.temp = doc["temp"] | 0;
          w.tmin = doc["min"] | 0;
          w.tmax = doc["max"] | 0;
          w.text = doc["text"] | "";
          w.icon = doc["icon"] | "cloud";
          xSemaphoreTake(g_mtx, portMAX_DELAY);
          g_weather = w;
          xSemaphoreGive(g_mtx);
          Serial.printf("[weather] %d°C %s %d~%d\n", w.temp, w.text.c_str(), w.tmin, w.tmax);
          wait = WEATHER_REFRESH_MS;
        } else {
          LOGE("weather GET -> %d", status);
        }
        http.end();
      }
    }
    vTaskDelay(pdMS_TO_TICKS(wait));
  }
}

static void chatTask(void*) {
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    if (g_warmupRequested) {
      g_warmupRequested = false;
      doWarmup();
      continue;
    }
    if (g_resetRequested) {
      g_resetRequested = false;
      doReset();
    } else if (g_storyRequested) {
      g_storyRequested = false;
      chatText("给我讲一个一百字左右的小故事", millis());
    } else if (WiFi.isConnected()) {
      bool autoMode = g_mode == Mode::Auto;
      // Utterances that began inside the follow-up window skip the wake word.
      bool requireWake = autoMode && g_recStartedAt >= g_followUntil;
      doChat(g_recLen, autoMode, requireWake);
    } else {
      setError("WiFi 未连接");
    }
    g_phase = Phase::Idle;
  }
}

// ---------------------------------------------------------------- drawing helpers

static uint16_t blend(uint16_t a, uint16_t b, float t) {  // t=0 -> a, t=1 -> b
  t = constrain(t, 0.0f, 1.0f);
  int ar = a >> 11, ag = (a >> 5) & 63, ab = a & 31;
  int br = b >> 11, bg = (b >> 5) & 63, bb = b & 31;
  return ((int)(ar + (br - ar) * t) << 11) | ((int)(ag + (bg - ag) * t) << 5) | (int)(ab + (bb - ab) * t);
}

static void buildBackground() {
  background.setColorDepth(16);
  background.setPsram(true);
  background.createSprite(SCREEN_W, SCREEN_H);
  for (int y = 0; y < SCREEN_H; y++) {
    for (int x = 0; x < SCREEN_W; x++) {
      float d = sqrtf((x - BG_CX) * (x - BG_CX) + (y - BG_CY) * (y - BG_CY));
      background.drawPixel(x, y, blend(C_BG_CENTER, C_BG_EDGE, d / 210.0f));
    }
  }
}

static void drawText(const String& s, int x, int y, uint16_t color, textdatum_t datum = top_left) {
  canvas.setTextColor(color);
  canvas.setTextDatum(datum);
  canvas.drawString(s, x, y);
}

// Greedy UTF-8 aware wrap using the current font's metrics.
static std::vector<String> wrapText(const String& text, int maxW) {
  std::vector<String> lines;
  String line;
  size_t i = 0;
  while (i < text.length()) {
    uint8_t c = text[i];
    size_t len = c < 0x80 ? 1 : (c >> 5) == 0x6 ? 2 : (c >> 4) == 0xE ? 3 : 4;
    String ch = text.substring(i, i + len);
    i += len;
    if (line.length() && canvas.textWidth(line + ch) > maxW) {
      lines.push_back(line);
      line = "";
    }
    line += ch;
  }
  if (line.length()) lines.push_back(line);
  return lines;
}

// 4-bar Wi-Fi signal meter with its bottom-left corner at (x, baseY); red cross when offline.
// Battery outline with a level fill, percentage and a bolt while charging; empty outline
// when no pack is connected (or the power switch is off). (x, y) is the top-left.
static void drawBattery(int x, int y) {
  const int w = 17, h = 9;
  battery::State b = battery::get();
  uint16_t frame = b.present ? C_SUB : 0x4208;
  canvas.drawRect(x, y, w, h, frame);
  canvas.fillRect(x + w, y + 3, 2, h - 6, frame);
  if (!b.present) return;
  uint16_t fill = b.percent > 40 ? C_GREEN : b.percent > 20 ? C_YELLOW : C_RED;
  int fw = max(1, (w - 4) * b.percent / 100);
  canvas.fillRect(x + 2, y + 2, fw, h - 4, fill);
  if (b.charging) {
    const int cx = x + w / 2;
    canvas.fillTriangle(cx + 1, y - 1, cx - 4, y + 5, cx, y + 5, C_YELLOW);
    canvas.fillTriangle(cx, y + 3, cx + 4, y + 3, cx - 1, y + h + 1, C_YELLOW);
  }
  canvas.setFont(&fonts::Font0);
  char buf[6];
  snprintf(buf, sizeof(buf), "%d%%", b.percent);
  drawText(buf, x + w + 5, y + 1, C_SUB);
}

static void drawWifiBars(int x, int baseY) {
  static int rssi = 0;
  static uint32_t sampledAt = 0;
  bool up = WiFi.isConnected();
  if (millis() - sampledAt > 1000) {  // RSSI is a driver call; sample it once a second
    rssi = up ? WiFi.RSSI() : 0;
    sampledAt = millis();
  }
  if (!up) {
    canvas.drawLine(x, baseY - 8, x + 8, baseY, C_RED);
    canvas.drawLine(x, baseY, x + 8, baseY - 8, C_RED);
    return;
  }
  int bars = rssi >= -55 ? 4 : rssi >= -65 ? 3 : rssi >= -75 ? 2 : rssi >= -85 ? 1 : 0;
  uint16_t lit = bars >= 3 ? C_GREEN : bars == 2 ? C_YELLOW : C_RED;
  for (int i = 0; i < 4; i++) {
    int h = 3 + i * 2;
    canvas.fillRect(x + i * 3, baseY - h, 2, h, i < bars ? lit : 0x31A6);
  }
}

// Round paw button beside the husky plus three dots marking the current skin.
static void drawSkinButton() {
  const int cx = SKIN_BTN_X, cy = SKIN_BTN_Y;
  canvas.fillCircle(cx, cy, SKIN_BTN_R, blend(C_PINK, C_BG_CENTER, 0.8f));
  canvas.drawCircle(cx, cy, SKIN_BTN_R, C_PINK);
  canvas.fillEllipse(cx, cy + 3, 4, 3, C_PINK);
  canvas.fillCircle(cx - 5, cy - 1, 2, C_PINK);
  canvas.fillCircle(cx - 2, cy - 5, 2, C_PINK);
  canvas.fillCircle(cx + 2, cy - 5, 2, C_PINK);
  canvas.fillCircle(cx + 5, cy - 1, 2, C_PINK);
  for (int i = 0; i < (int)Skin::Count; i++) {
    int dx = cx + (i - 1) * 7;
    canvas.fillCircle(dx, cy + SKIN_BTN_R + 6, 2, i == (int)g_skin ? C_PINK : C_PURPLE_DIM);
  }
}

static bool onSkinButton(int32_t x, int32_t y) {
  int32_t dx = x - SKIN_BTN_X, dy = y - SKIN_BTN_Y;
  return dx * dx + dy * dy <= SKIN_BTN_HIT * SKIN_BTN_HIT;
}

static void nextSkin() {
  g_skin = static_cast<Skin>(((uint8_t)g_skin + 1) % (uint8_t)Skin::Count);
  prefs.putUChar("skin", (uint8_t)g_skin);
  Serial.printf("[skin] -> %u\n", (uint8_t)g_skin);
}

// Small line-art weather glyphs, roughly 24 px, centred on (cx, cy).
static void drawWeatherIcon(const String& kind, int cx, int cy) {
  const uint16_t sun = C_YELLOW, cloud = 0xCE79, rain = 0x7E3F;
  auto drawCloud = [&](int x, int y) {
    canvas.fillCircle(x - 5, y + 1, 5, cloud);
    canvas.fillCircle(x + 1, y - 3, 7, cloud);
    canvas.fillCircle(x + 7, y + 2, 5, cloud);
    canvas.fillRoundRect(x - 10, y + 1, 22, 7, 3, cloud);
  };
  if (kind == "sun") {
    canvas.fillCircle(cx, cy, 6, sun);
    for (int i = 0; i < 8; i++) {
      float a = i * PI / 4;
      canvas.drawLine(cx + cosf(a) * 9, cy + sinf(a) * 9, cx + cosf(a) * 12, cy + sinf(a) * 12, sun);
    }
  } else if (kind == "moon") {
    canvas.fillCircle(cx, cy, 8, sun);
    canvas.fillCircle(cx + 4, cy - 3, 7, C_BG_CENTER);
  } else if (kind == "partly") {
    canvas.fillCircle(cx - 4, cy - 4, 6, sun);
    drawCloud(cx + 2, cy + 2);
  } else if (kind == "fog") {
    for (int i = 0; i < 4; i++) canvas.drawFastHLine(cx - 10 + (i % 2) * 3, cy - 6 + i * 4, 18, cloud);
  } else {
    drawCloud(cx, cy - 3);
    if (kind == "rain") {
      for (int i = -1; i <= 1; i++) canvas.drawLine(cx + i * 6, cy + 8, cx + i * 6 - 2, cy + 12, rain);
    } else if (kind == "snow") {
      for (int i = -1; i <= 1; i++) canvas.fillCircle(cx + i * 6, cy + 10, 1, TFT_WHITE);
    } else if (kind == "thunder") {
      canvas.fillTriangle(cx + 1, cy + 5, cx - 4, cy + 11, cx, cy + 11, sun);
      canvas.fillTriangle(cx, cy + 9, cx + 4, cy + 9, cx - 1, cy + 15, sun);
    }
  }
}

// Left info card: time, date, weather icon + temperature, condition and today's range.
static void drawInfoCard() {
  const int x = 12, y = 38;
  canvas.drawFastVLine(x - 4, y + 2, 100, blend(C_PURPLE, C_BG_CENTER, 0.6f));

  tm now;
  bool timeOk = getLocalTime(&now, 0) && now.tm_year + 1900 >= 2024;
  char buf[24];
  if (timeOk) {
    snprintf(buf, sizeof(buf), "%02d:%02d", now.tm_hour, now.tm_min);
  } else {
    strcpy(buf, "--:--");
  }
  canvas.setFont(&fonts::Font4);
  drawText(buf, x, y, C_TEXT);
  canvas.setFont(&fonts::efontCN_12);
  if (timeOk) {
    snprintf(buf, sizeof(buf), "%d/%d %s", now.tm_mon + 1, now.tm_mday, WEEKDAYS[now.tm_wday]);
    drawText(buf, x, y + 28, C_SUB);
  }

  xSemaphoreTake(g_mtx, portMAX_DELAY);
  WeatherNow w = g_weather;
  xSemaphoreGive(g_mtx);
  if (!w.valid) {
    drawText("天气加载中", x, y + 52, C_SUB);
    return;
  }
  drawWeatherIcon(w.icon, x + 11, y + 58);
  canvas.setFont(&fonts::FreeSansBold12pt7b);
  snprintf(buf, sizeof(buf), "%d", w.temp);
  drawText(buf, x + 28, y + 47, C_YELLOW);
  int tw = canvas.textWidth(buf);
  canvas.drawCircle(x + 28 + tw + 4, y + 51, 2, C_YELLOW);  // GFX fonts have no degree sign
  canvas.setFont(&fonts::efontCN_12);
  drawText(w.text, x, y + 76, C_SUB);
  snprintf(buf, sizeof(buf), "%d~%d°", w.tmin, w.tmax);
  drawText(buf, x, y + 91, C_SUB);
}

static void drawHeader() {
  canvas.setFont(&fonts::efontCN_16);
  drawText("小奇", 8, 5, C_PURPLE);
  canvas.setFont(&fonts::Font0);
  drawText("HUSKY", 44, 11, C_PURPLE_DIM);

  // HOLD / AUTO segmented toggle
  canvas.drawRect(TOGGLE_X, TOGGLE_Y, TOGGLE_W, TOGGLE_H, C_PURPLE);
  int half = TOGGLE_W / 2;
  bool autoOn = g_mode == Mode::Auto;
  canvas.fillRect(TOGGLE_X + (autoOn ? half : 0) + 1, TOGGLE_Y + 1, half - 1, TOGGLE_H - 2, C_PURPLE);
  canvas.setFont(&fonts::Font2);
  drawText("HOLD", TOGGLE_X + half / 2, TOGGLE_Y + TOGGLE_H / 2 + 1, autoOn ? C_PURPLE_DIM : TFT_BLACK, middle_center);
  drawText("AUTO", TOGGLE_X + half + half / 2, TOGGLE_Y + TOGGLE_H / 2 + 1, autoOn ? TFT_BLACK : C_PURPLE_DIM,
           middle_center);

  // Reset glyph: open circle with an arrow head
  const int rx = 305, ry = 13;
  canvas.fillArc(rx, ry, 5, 7, 50, 330, C_GREEN);
  canvas.fillTriangle(rx + 7, ry - 6, rx + 9, ry + 1, rx + 2, ry - 1, C_GREEN);

  drawWifiBars(80, 17);
  drawBattery(98, 8);
}

// Centered, wrapped subtitle block; returns number of lines drawn.
static void drawSubtitle(const String& text, int y, int maxLines, uint16_t color, float scroll = 0) {
  if (text.isEmpty()) return;
  canvas.setFont(&fonts::efontCN_16);
  std::vector<String> lines = wrapText(text, TEXT_W);
  int first = 0;
  if ((int)lines.size() > maxLines) {
    first = constrain((int)(scroll * (lines.size() - maxLines + 1)), 0, (int)lines.size() - maxLines);
  }
  for (int i = 0; i < maxLines && first + i < (int)lines.size(); i++) {
    drawText(lines[first + i], SCREEN_W / 2, y + i * LINE_H, color, top_center);
  }
}

static String ellipsize(const String& s, int maxW) {
  canvas.setFont(&fonts::efontCN_16);
  if (canvas.textWidth(s) <= maxW) return s;
  std::vector<String> lines = wrapText(s, maxW - canvas.textWidth("…"));
  return lines.empty() ? s : lines[0] + "…";
}

static Frame emotionFrame(const String& e, bool talking) {
  int base = e == "happy"       ? (int)Frame::HAPPY
             : e == "surprised" ? (int)Frame::SURPRISED
             : e == "sad"       ? (int)Frame::SAD
             : e == "shy"       ? (int)Frame::SHY
             : e == "smug"      ? (int)Frame::SMUG
                                : (int)Frame::CALM;
  return static_cast<Frame>(base + (talking ? 1 : 0));  // *_TALK follows each base frame
}

// Picks the husky frame for the current state; called once per rendered frame.
static Frame pickFrame(Phase phase, float micLevel, const String& emotion) {
  static uint32_t nextBlink = 3000, blinkUntil = 0;
  uint32_t now = millis();
  switch (phase) {
    case Phase::Listening:
      return (micLevel > LISTEN_WAVE_LEVEL || (now / 300) % 2) ? Frame::LISTEN_B : Frame::LISTEN;
    case Phase::Thinking:
      if (g_wakeCheck) return Frame::LISTEN;  // not addressed yet: still just perking up
      return (now / 400) % 2 ? Frame::THINK_B : Frame::THINK;
    case Phase::Speaking:
      return emotionFrame(emotion, g_play.level > TALK_LEVEL);
    case Phase::Idle:
    default:
      if (now < g_emotionUntil) return emotionFrame(emotion, false);
      if (now - g_lastActivity > SLEEPY_AFTER_MS) return Frame::SLEEPY;
      if (now >= nextBlink) {
        blinkUntil = now + BLINK_MS;
        nextBlink = now + random(2500, 6000);
      }
      return now < blinkUntil ? Frame::BLINK : Frame::IDLE;
  }
}

static void drawCharacter(Frame f) {
  const size_t frameBytes = SKIN_FRAME_SIZE * SKIN_FRAME_SIZE * 2;
  const uint8_t* img = skins_bin_start + ((size_t)g_skin * SKIN_FRAME_COUNT + (size_t)f) * frameBytes;
  canvas.pushImage(SKIN_FRAME_X, SKIN_FRAME_Y, SKIN_FRAME_SIZE, SKIN_FRAME_SIZE,
                   reinterpret_cast<const lgfx::swap565_t*>(img));
}

static void drawFrame(float micLevel, float recSeconds) {
  xSemaphoreTake(g_mtx, portMAX_DELAY);
  String you = g_lastUser, ai = g_lastAi, emotion = g_emotion;
  String err = millis() - g_errorAt < ERROR_SHOW_MS ? g_error : String();
  xSemaphoreGive(g_mtx);
  Phase phase = g_phase;
  bool autoOn = g_mode == Mode::Auto;
  float t = millis() / 1000.0f;

  background.pushSprite(&canvas, 0, 0);
  drawCharacter(pickFrame(phase, micLevel, emotion));
  drawInfoCard();
  drawSkinButton();

  // Status colour + label
  uint16_t color;
  String label;
  switch (phase) {
    case Phase::Listening:
      color = C_GREEN;
      label = "LISTENING " + String(recSeconds, 1) + "s";
      break;
    case Phase::Thinking:
      color = g_wakeCheck ? C_GREEN : C_YELLOW;
      label = g_wakeCheck ? "LISTENING" : "THINKING";
      break;
    case Phase::Speaking:
      color = C_CYAN;
      label = "SPEAKING  TAP TO STOP";
      break;
    case Phase::Idle:
    default:
      color = C_PURPLE;
      label = !autoOn ? "STANDBY" : millis() < g_followUntil ? "GO AHEAD  I'M LISTENING" : "AUTO  WAKE WORD ON";
      break;
  }

  drawHeader();

  // State label with a small leading pip
  canvas.setFont(&fonts::Font2);
  uint16_t labelColor = phase == Phase::Idle && !autoOn ? C_PURPLE_DIM : color;
  int lw = canvas.textWidth(label);
  int lx = SCREEN_W / 2 - lw / 2 + 6;
  drawText(label, lx, STATE_Y, labelColor, top_left);
  if (phase == Phase::Speaking) {
    canvas.fillTriangle(lx - 12, STATE_Y + 3, lx - 12, STATE_Y + 11, lx - 5, STATE_Y + 7, labelColor);
  } else {
    canvas.fillCircle(lx - 8, STATE_Y + 7, 3, labelColor);
  }

  // Subtitles: last thing you said, then the reply (scrolls with playback).
  if (err.length()) {
    drawSubtitle(err, AI_Y, AI_MAX_LINES, C_RED);
  } else if (you.isEmpty() && ai.isEmpty()) {
    drawSubtitle(autoOn ? "叫“小奇小奇”唤醒我" : "按住屏幕说话，松开发送", AI_Y - 6, 1, C_SUB);
  } else {
    canvas.setFont(&fonts::efontCN_16);
    if (you.length()) drawText(ellipsize(you, TEXT_W), SCREEN_W / 2, YOU_Y, phase == Phase::Listening ? C_GREEN : C_SUB, top_center);
    uint16_t aiColor = phase == Phase::Listening ? C_SUB : C_TEXT;
    drawSubtitle(ai, AI_Y, AI_MAX_LINES, aiColor, phase == Phase::Speaking ? g_chunkFrom + g_play.progress * g_chunkSpan : 0);
  }

  // Base line
  if (phase == Phase::Listening) {
    for (int x = 0; x < SCREEN_W; x++) {
      float k = fabsf(fmodf(x / 160.0f + t, 2.0f) - 1.0f);
      canvas.drawFastVLine(x, SCREEN_H - 3, 3, blend(C_GREEN, C_PURPLE, k));
    }
  } else {
    canvas.fillRect(0, SCREEN_H - 3, SCREEN_W, 3, phase == Phase::Idle ? blend(C_PURPLE, C_BG_EDGE, 0.6f) : color);
  }
}

// Sends the current frame as raw big-endian RGB565 after a "SNAP w h n" header line.
static void dumpFrame() {
  const size_t n = SCREEN_W * SCREEN_H * 2;
  Serial.printf("SNAP %d %d %u\n", SCREEN_W, SCREEN_H, n);
  const uint8_t* buf = static_cast<const uint8_t*>(canvas.getBuffer());
  for (size_t off = 0; off < n;) {
    size_t w = Serial.write(buf + off, min<size_t>(1024, n - off));
    if (w == 0) delay(1);
    off += w;
  }
  Serial.flush();
  Serial.println("\nSNAP END");
}

// ---------------------------------------------------------------- recording

static Vad g_vad;
static int16_t g_preroll[PREROLL_CHUNKS][audio::CHUNK_FRAMES];
static size_t g_prerollLen[PREROLL_CHUNKS];
static int g_prerollHead = 0;

static void appendRec(const int16_t* s, size_t n) {
  n = min(n, REC_CAPACITY - g_recLen);
  memcpy(g_rec + g_recLen, s, n * sizeof(int16_t));
  g_recLen += n;
}

static void sendRecording() {
  Serial.printf("[rec] %.1fs mode=%s\n", g_recLen / (float)audio::SAMPLE_RATE, g_mode == Mode::Auto ? "auto" : "hold");
  g_phase = Phase::Thinking;
  xTaskNotifyGive(g_chatTask);
}

static void startHoldListening() {
  audio::flushInput();
  g_recStartedAt = millis();
  g_recLen = 0;
  g_phase = Phase::Listening;
}

static void setMode(Mode m) {
  g_mode = m;
  prefs.putUChar("auto", m == Mode::Auto);
  g_vad.reset();
  if (g_phase == Phase::Listening) g_phase = Phase::Idle;  // drop a half-recorded clip
  audio::flushInput();
  Serial.printf("[mode] %s\n", m == Mode::Auto ? "auto" : "hold");
}

// Serial test hook: send a synthetic 1 s tone through the whole pipeline.
static void sendTestTone() {
  const size_t n = audio::SAMPLE_RATE;
  for (size_t i = 0; i < n; i++) g_rec[i] = (int16_t)(3000 * sinf(2 * PI * 440 * i / audio::SAMPLE_RATE));
  g_recLen = n;
  sendRecording();
}

// ---------------------------------------------------------------- main

void setup() {
  Serial.begin(115200);
  lcd.init();
  lcd.setRotation(1);
  lcd.setBrightness(200);
  canvas.setColorDepth(16);
  canvas.setPsram(true);
  g_rec = static_cast<int16_t*>(ps_malloc(REC_CAPACITY * sizeof(int16_t)));
  if (!canvas.createSprite(SCREEN_W, SCREEN_H) || !g_rec) {
    lcd.fillScreen(TFT_RED);
    LOGE("PSRAM alloc failed, psram=%u", ESP.getPsramSize());
    for (;;) delay(1000);
  }
  buildBackground();
  g_mtx = xSemaphoreCreateMutex();
  prefs.begin("xiaoqi");
  g_mode = prefs.getUChar("auto", 0) ? Mode::Auto : Mode::Hold;
  g_skin = static_cast<Skin>(prefs.getUChar("skin", 0) % (uint8_t)Skin::Count);
  randomSeed(esp_random());

  battery::begin();
  bool audioOk = audio::begin();
  Serial.printf("[boot] audio=%s mode=%s server=%s\n", audioOk ? "ok" : "FAILED",
                g_mode == Mode::Auto ? "auto" : "hold", CHAT_SERVER);
  if (!audioOk) setError("音频初始化失败");

  WiFi.mode(WIFI_STA);
  // Modem sleep throttles uploads badly (1 s of audio took ~3 s to POST).
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  // The home SSID is served by several mesh APs; the default fast scan joins the
  // first one heard (often a far one at ~-80 dBm), so scan all channels and pick
  // the strongest.
  WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
  WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  configTzTime(TZ_INFO, "ntp.aliyun.com", "pool.ntp.org", "time.apple.com");
  xTaskCreatePinnedToCore(chatTask, "chat", 16 * 1024, nullptr, 2, &g_chatTask, 0);
  xTaskCreatePinnedToCore(weatherTask, "weather", 8 * 1024, nullptr, 1, nullptr, 0);
}

void loop() {
  static bool touchWasDown = false;
  static uint32_t pressAt = 0;
  static uint32_t lastFrame = 0;
  static uint32_t holdoffUntil = 0;
  static Phase lastPhase = Phase::Idle;
  static bool lastWifi = false;
  static bool vadDebug = false;
  static uint32_t lastVadLog = 0;
  static float micLevel = 0;

  uint32_t now = millis();
  Phase phase = g_phase;
  if (phase == Phase::Idle && lastPhase != Phase::Idle) {
    // Back from speaking/thinking: drop whatever the mic heard meanwhile.
    audio::flushInput();
    g_vad.reset();
    holdoffUntil = now + ECHO_HOLDOFF_MS;
  }
  lastPhase = phase;

  // ---- input
  int32_t x, y;
  bool touchDown = lcd.getTouch(&x, &y);
  bool pressed = touchDown && !touchWasDown;
  bool released = !touchDown && touchWasDown;
  touchWasDown = touchDown;

  bool capture = false;
  while (Serial.available()) {
    switch (Serial.read()) {
      case 'c': capture = true; break;
      case 't':
        if (g_phase == Phase::Idle) sendTestTone();
        break;
      case 's':  // exercises multi-sentence playback without a microphone
        if (g_phase == Phase::Idle) {
          g_storyRequested = true;
          g_phase = Phase::Thinking;
          xTaskNotifyGive(g_chatTask);
        }
        break;
      case 'm': setMode(g_mode == Mode::Auto ? Mode::Hold : Mode::Auto); break;
      case 'v': vadDebug = !vadDebug; break;
      case 'k': nextSkin(); break;
      case 'b': {
        battery::State b = battery::get();
        Serial.printf("[bat] present=%d v=%.3f pct=%d charging=%d\n", b.present, b.volts, b.percent, b.charging);
        break;
      }
      default: break;
    }
  }

  if (touchDown || g_phase != Phase::Idle) g_lastActivity = now;

  if (pressed && y < TOP_HIT_H && x >= TOGGLE_X) {
    if (x >= RESET_X) {
      if (g_phase == Phase::Idle) {
        g_resetRequested = true;
        g_phase = Phase::Thinking;
        xTaskNotifyGive(g_chatTask);
      }
    } else {
      setMode(g_mode == Mode::Auto ? Mode::Hold : Mode::Auto);
    }
    pressed = false;  // consumed
  }
  if (pressed && onSkinButton(x, y)) {
    nextSkin();
    pressed = false;  // don't also start a recording or interrupt playback
  }

  switch (g_phase) {
    case Phase::Speaking:
      if (pressed) g_abortPlayback = true;
      break;
    case Phase::Idle:
      if (pressed && g_mode == Mode::Hold) {
        pressAt = now;
        startHoldListening();
      }
      break;
    case Phase::Listening:
      if (g_mode == Mode::Hold && released) {
        if (now - pressAt < MIN_HOLD_MS || g_recLen == 0) {
          g_phase = Phase::Idle;
        } else {
          sendRecording();
        }
      } else if (g_mode == Mode::Auto && pressed) {
        g_vad.reset();
        sendRecording();  // tap to cut the utterance short
      }
      break;
    case Phase::Thinking:
      break;
  }

  // ---- microphone: drain everything buffered so rendering never drops audio
  bool hold = g_mode == Mode::Hold;
  bool capturing = g_phase == Phase::Listening || (!hold && g_phase == Phase::Idle);
  if (capturing) {
    int16_t chunk[audio::CHUNK_FRAMES];
    size_t n;
    while (capturing && (n = audio::readChunk(chunk, 0)) > 0) {
      int64_t sq = 0;
      for (size_t i = 0; i < n; i++) sq += (int32_t)chunk[i] * chunk[i];
      float rms = sqrtf((float)sq / n);
      micLevel = micLevel * 0.6f + rms * 0.4f;

      if (hold) {
        appendRec(chunk, n);
        if (g_recLen >= REC_CAPACITY) sendRecording();
      } else if (millis() >= holdoffUntil) {
        Vad::Event ev = g_vad.feed(rms);
        if (ev == Vad::Event::Start) {
          g_recStartedAt = millis();
          // Seed the clip with the pre-roll so the first syllable isn't clipped.
          g_recLen = 0;
          for (int k = 0; k < PREROLL_CHUNKS; k++) {
            int idx = (g_prerollHead + k) % PREROLL_CHUNKS;
            appendRec(g_preroll[idx], g_prerollLen[idx]);
          }
          g_phase = Phase::Listening;
        }
        if (g_vad.active() || ev == Vad::Event::End) appendRec(chunk, n);
        if (ev == Vad::Event::End) {
          sendRecording();
        } else if (ev == Vad::Event::TooShort) {
          g_phase = Phase::Idle;
        }
        memcpy(g_preroll[g_prerollHead], chunk, n * sizeof(int16_t));
        g_prerollLen[g_prerollHead] = n;
        g_prerollHead = (g_prerollHead + 1) % PREROLL_CHUNKS;
        if (vadDebug && millis() - lastVadLog > 250) {
          Serial.printf("[vad] rms=%.0f noise=%.0f start>%.0f keep>%.0f active=%d\n", rms, g_vad.noise(),
                        g_vad.startThreshold(), g_vad.keepThreshold(), g_vad.active());
          lastVadLog = millis();
        }
      }
      capturing = g_phase == Phase::Listening || (!hold && g_phase == Phase::Idle);
    }
  } else {
    micLevel *= 0.8f;
  }

  battery::update();

  bool wifi = WiFi.isConnected();
  if (wifi != lastWifi) {
    Serial.printf("[wifi] %s ip=%s bssid=%s ch=%d rssi=%d\n", wifi ? "connected" : "disconnected",
                  WiFi.localIP().toString().c_str(), WiFi.BSSIDstr().c_str(), WiFi.channel(), WiFi.RSSI());
    lastWifi = wifi;
    if (wifi && g_phase == Phase::Idle) {
      g_warmupRequested = true;
      xTaskNotifyGive(g_chatTask);
    }
  }

  // ---- render (the husky is always animated)
  if (capture || now - lastFrame >= FRAME_MS) {
    drawFrame(micLevel, g_recLen / (float)audio::SAMPLE_RATE);
    canvas.pushSprite(0, 0);
    if (capture) dumpFrame();
    lastFrame = now;
  }
  delay(2);
}
