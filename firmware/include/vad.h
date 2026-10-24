#pragma once
// Energy-based voice activity detector for AUTO mode, fed one 16 ms chunk RMS at a time.
// It tracks the ambient noise floor and opens on a sustained rise above it.
#include <algorithm>

class Vad {
 public:
  enum class Event { None, Start, End, TooShort };

  // Thresholds are max(noise * factor, floor) in int16 RMS units. The board's
  // quiet-room noise floor measures rms ~4. Floors are a first guess for speech at
  // arm's length; tune with the 'v' serial command if it misses or over-triggers.
  static constexpr float START_FACTOR = 3.0f;
  static constexpr float START_FLOOR = 60.0f;
  static constexpr float KEEP_FACTOR = 2.0f;
  static constexpr float KEEP_FLOOR = 35.0f;
  static constexpr int START_CHUNKS = 3;      // 48 ms above start threshold opens
  static constexpr int END_CHUNKS = 55;       // ~880 ms below keep threshold closes
  static constexpr int MIN_SPEECH_CHUNKS = 25;  // utterances under ~400 ms are dropped
  static constexpr int MAX_CHUNKS = 15 * 1000 / 16;
  static constexpr float NOISE_ALPHA = 0.05f;

  Event feed(float rms) {
    if (!active_) {
      if (rms > startThreshold()) {
        if (++above_ >= START_CHUNKS) {
          active_ = true;
          total_ = speech_ = above_;
          below_ = 0;
          return Event::Start;
        }
      } else {
        above_ = 0;
        // Only learn from quiet chunks so speech doesn't drag the floor up.
        noise_ += NOISE_ALPHA * (rms - noise_);
      }
      return Event::None;
    }
    total_++;
    if (rms > keepThreshold()) {
      speech_++;
      below_ = 0;
    } else {
      below_++;
    }
    if (below_ >= END_CHUNKS || total_ >= MAX_CHUNKS) {
      bool longEnough = speech_ >= MIN_SPEECH_CHUNKS;
      reset();
      return longEnough ? Event::End : Event::TooShort;
    }
    return Event::None;
  }

  void reset() {
    active_ = false;
    above_ = below_ = total_ = speech_ = 0;
  }

  bool active() const { return active_; }
  float noise() const { return noise_; }
  float startThreshold() const { return std::max(noise_ * START_FACTOR, START_FLOOR); }
  float keepThreshold() const { return std::max(noise_ * KEEP_FACTOR, KEEP_FLOOR); }

 private:
  bool active_ = false;
  float noise_ = 30.0f;
  int above_ = 0, below_ = 0, total_ = 0, speech_ = 0;
};
