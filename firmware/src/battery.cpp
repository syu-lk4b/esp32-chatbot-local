#include "battery.h"

namespace battery {
namespace {

constexpr int PIN = 9;
constexpr float DIVIDER = 2.0f;           // R14 = R15 = 200k
constexpr int SAMPLES = 16;
constexpr uint32_t SAMPLE_MS = 2000;
constexpr float SMOOTHING = 0.3f;         // EMA weight of each new reading
constexpr float PRESENT_MIN_V = 2.5f;     // below this the pack is absent or switched off
constexpr uint32_t TREND_WINDOW_MS = 60000;
constexpr float CHARGE_RISE_V = 0.010f;   // rise per window that counts as charging
constexpr uint32_t CHARGE_HOLD_MS = 150000;

struct Point {
  float v;
  int pct;
};
// Typical single-cell LiPo resting voltages.
constexpr Point CURVE[] = {{4.20f, 100}, {4.10f, 90}, {4.00f, 80}, {3.90f, 65}, {3.80f, 50}, {3.75f, 40},
                           {3.70f, 30},  {3.65f, 20}, {3.60f, 12}, {3.50f, 5},  {3.30f, 0}};

State g_state;
float g_ema = 0;
uint32_t g_lastSample = 0;
uint32_t g_windowStart = 0;
float g_windowStartV = 0;
uint32_t g_chargingUntil = 0;

float readVolts() {
  uint32_t mv = 0;
  for (int i = 0; i < SAMPLES; i++) mv += analogReadMilliVolts(PIN);
  return mv / (float)SAMPLES / 1000.0f * DIVIDER;
}

}  // namespace

int percentFor(float v) {
  if (v >= CURVE[0].v) return 100;
  for (size_t i = 1; i < sizeof(CURVE) / sizeof(CURVE[0]); i++) {
    if (v >= CURVE[i].v) {
      const Point& hi = CURVE[i - 1];
      const Point& lo = CURVE[i];
      return lo.pct + (int)((v - lo.v) / (hi.v - lo.v) * (hi.pct - lo.pct) + 0.5f);
    }
  }
  return 0;
}

void begin() {
  analogSetPinAttenuation(PIN, ADC_11db);  // pin sees up to ~2.1 V
  g_ema = readVolts();
  g_windowStartV = g_ema;
  g_windowStart = g_lastSample = millis();
}

void update() {
  uint32_t now = millis();
  if (now - g_lastSample < SAMPLE_MS) return;
  g_lastSample = now;

  float v = readVolts();
  g_ema += SMOOTHING * (v - g_ema);
  g_state.volts = g_ema;
  g_state.present = g_ema >= PRESENT_MIN_V;
  g_state.percent = g_state.present ? percentFor(g_ema) : 0;

  if (now - g_windowStart >= TREND_WINDOW_MS) {
    if (g_state.present && g_ema - g_windowStartV >= CHARGE_RISE_V) g_chargingUntil = now + CHARGE_HOLD_MS;
    g_windowStart = now;
    g_windowStartV = g_ema;
  }
  g_state.charging = g_state.present && now < g_chargingUntil;
}

State get() { return g_state; }

}  // namespace battery
