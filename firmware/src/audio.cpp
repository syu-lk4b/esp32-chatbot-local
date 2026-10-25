#include "audio.h"

#include <driver/i2s.h>

#define LGFX_USE_V1
#include <LovyanGFX.hpp>

namespace audio {
namespace {

constexpr int I2C_PORT = 0;  // shared with FT6336 touch, owned by LovyanGFX
constexpr uint8_t ES8311_ADDR = 0x18;
constexpr uint32_t I2C_FREQ = 400000;
constexpr i2s_port_t I2S_PORT = I2S_NUM_0;

constexpr int PIN_AMP_EN = 1;  // FM8002E shutdown, active low
constexpr int PIN_MCLK = 4;
constexpr int PIN_BCLK = 5;
constexpr int PIN_LRCK = 7;
// Official GPIO table: I2S_DO = IO8 (to codec DSDIN), I2S_DI = IO6 (from codec ASDOUT).
constexpr int PIN_DOUT = 8;
constexpr int PIN_DIN = 6;

bool writeReg(uint8_t reg, uint8_t val) {
  if (!lgfx::i2c::writeRegister8(I2C_PORT, ES8311_ADDR, reg, val, 0, I2C_FREQ).has_value()) {
    Serial.printf("[err] es8311 write 0x%02X=0x%02X failed\n", reg, val);
    return false;
  }
  return true;
}

int readReg(uint8_t reg) {
  auto r = lgfx::i2c::readRegister8(I2C_PORT, ES8311_ADDR, reg, I2C_FREQ);
  return r.has_value() ? r.value() : -1;
}

// Same sequence as projects/es3c28p-audio-test (Espressif es8311 driver, slave
// mode, MCLK pin at 4.096 MHz = 256 fs, analog mic on MIC1P).
bool initCodec() {
  if (readReg(0xFD) != 0x83 || readReg(0xFE) != 0x11) {
    Serial.println("[err] ES8311 not detected");
    return false;
  }
  static const uint8_t seq[][2] = {
      {0x44, 0x08}, {0x44, 0x08}, {0x01, 0x30}, {0x02, 0x00}, {0x03, 0x10}, {0x16, 0x24}, {0x04, 0x10},
      {0x05, 0x00}, {0x0B, 0x00}, {0x0C, 0x00}, {0x10, 0x1F}, {0x11, 0x7F}, {0x00, 0x80}, {0x02, 0x00},
      {0x05, 0x00}, {0x03, 0x10}, {0x04, 0x10}, {0x07, 0x00}, {0x08, 0xFF}, {0x06, 0x03}, {0x13, 0x10},
      {0x1B, 0x0A}, {0x1C, 0x6A}, {0x01, 0x3F}, {0x09, 0x0C}, {0x0A, 0x0C}, {0x17, 0xBF}, {0x0E, 0x02},
      {0x12, 0x00}, {0x14, 0x1A}, {0x0D, 0x01}, {0x15, 0x40}, {0x37, 0x08}, {0x45, 0x00},
      {0x32, 0xC8},  // DAC volume ~ +4.5 dB
  };
  for (auto& r : seq) {
    if (!writeReg(r[0], r[1])) return false;
  }
  return true;
}

bool initI2S() {
  i2s_config_t cfg = {};
  cfg.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX | I2S_MODE_RX);
  cfg.sample_rate = SAMPLE_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 12;  // ~190 ms of slack while the UI renders a frame
  cfg.dma_buf_len = CHUNK_FRAMES;
  cfg.tx_desc_auto_clear = true;
  cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;

  i2s_pin_config_t pc = {};
  pc.mck_io_num = PIN_MCLK;
  pc.bck_io_num = PIN_BCLK;
  pc.ws_io_num = PIN_LRCK;
  pc.data_out_num = PIN_DOUT;
  pc.data_in_num = PIN_DIN;

  esp_err_t err = i2s_driver_install(I2S_PORT, &cfg, 0, nullptr);
  if (err == ESP_OK) err = i2s_set_pin(I2S_PORT, &pc);
  if (err != ESP_OK) {
    Serial.printf("[err] i2s init: %s\n", esp_err_to_name(err));
    return false;
  }
  return true;
}

}  // namespace

bool begin() {
  pinMode(PIN_AMP_EN, OUTPUT);
  digitalWrite(PIN_AMP_EN, HIGH);
  return initI2S() && initCodec();
}

void flushInput() {
  int16_t buf[CHUNK_FRAMES * 2];
  size_t got;
  // DMA holds 12 x 256 frames; drain them plus a margin for the ADC to settle.
  for (int i = 0; i < 14; i++) i2s_read(I2S_PORT, buf, sizeof(buf), &got, portMAX_DELAY);
}

size_t readChunk(int16_t* out, uint32_t waitMs) {
  int16_t stereo[CHUNK_FRAMES * 2];
  size_t got = 0;
  i2s_read(I2S_PORT, stereo, sizeof(stereo), &got, pdMS_TO_TICKS(waitMs));
  size_t frames = got / 4;
  for (size_t i = 0; i < frames; i++) out[i] = stereo[2 * i];  // ADC drives the left slot
  return frames;
}

void speakerOn() { digitalWrite(PIN_AMP_EN, LOW); }

void speakerOff() {
  // Push silence through the DMA ring so the amp doesn't clip the tail.
  int16_t stereo[CHUNK_FRAMES * 2] = {};
  size_t written;
  for (int i = 0; i < 8; i++) i2s_write(I2S_PORT, stereo, sizeof(stereo), &written, portMAX_DELAY);
  digitalWrite(PIN_AMP_EN, HIGH);
}

bool play(const int16_t* pcm, size_t n, volatile bool* abort, PlayStats* stats, bool standalone) {
  int16_t stereo[CHUNK_FRAMES * 2];
  size_t written;
  bool completed = true;
  if (standalone) speakerOn();
  for (size_t off = 0; off < n; off += CHUNK_FRAMES) {
    if (abort && *abort) {
      completed = false;
      break;
    }
    size_t m = min(CHUNK_FRAMES, n - off);
    int peak = 0;
    for (size_t i = 0; i < m; i++) {
      stereo[2 * i] = stereo[2 * i + 1] = pcm[off + i];
      peak = max(peak, abs((int)pcm[off + i]));
    }
    i2s_write(I2S_PORT, stereo, m * 4, &written, portMAX_DELAY);
    if (stats) {
      stats->level = peak;
      stats->progress = (float)(off + m) / n;
    }
  }
  if (stats) stats->level = 0;
  if (standalone) speakerOff();
  return completed;
}

}  // namespace audio
