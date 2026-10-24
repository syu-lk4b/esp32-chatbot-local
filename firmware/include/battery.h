#pragma once
// LiPo gauge for the ES3C28P: IO9 reads the battery through a 200k/200k divider.
//
// The board has no USB-detect pin, so "charging" is inferred from a sustained rise in
// voltage; it shows up a minute or two after plugging in.
#include <Arduino.h>

namespace battery {

struct State {
  bool present = false;   // false: no pack, or the power switch is off
  bool charging = false;  // inferred from the voltage trend
  float volts = 0;
  int percent = 0;
};

void begin();

// Samples the ADC; call often, it rate-limits itself.
void update();

State get();

// Open-circuit LiPo curve, linear between points (exposed for logging/tests).
int percentFor(float volts);

}  // namespace battery
