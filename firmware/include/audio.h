#pragma once
// ES8311 codec + I2S for the ES3C28P: 16 kHz, 16-bit, mono at the API boundary.
#include <Arduino.h>

namespace audio {

constexpr uint32_t SAMPLE_RATE = 16000;
constexpr size_t CHUNK_FRAMES = 256;  // 16 ms per I2S transfer

// Live playback telemetry for the UI.
struct PlayStats {
  volatile int level = 0;          // peak of the chunk being played, 0..32767
  volatile float progress = 0.0f;  // 0..1
};

// Must run after lcd.init(): the codec shares I2C port 0 with the touch
// controller, which LovyanGFX has already brought up.
bool begin();

// Discards stale samples sitting in the RX DMA buffers.
void flushInput();

// Reads one chunk of up to CHUNK_FRAMES mono samples. With waitMs = 0 it only
// returns what is already buffered (possibly 0), so callers can drain the
// DMA ring without blocking the UI.
size_t readChunk(int16_t* out, uint32_t waitMs);

// Plays mono samples; returns early (false) if *abort becomes true. standalone = false
// leaves the amp alone, for clips played back to back between speakerOn()/speakerOff().
bool play(const int16_t* pcm, size_t n, volatile bool* abort = nullptr, PlayStats* stats = nullptr,
          bool standalone = true);

// Amp on / drain the DMA ring and amp off. Keeping the amp on across the sentences of a reply
// avoids a pop and a gap at every sentence; an underrun meanwhile plays silence (auto-clear).
void speakerOn();
void speakerOff();

}  // namespace audio
