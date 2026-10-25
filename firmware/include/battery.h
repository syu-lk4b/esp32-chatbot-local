#pragma once
// LiPo gauge for the ES3C28P: IO9 reads the battery through a 200k/200k divider.
//
// The board has no VBUS-detect pin. On a computer, "charging" comes from the native USB
// seeing a host (immediate); on a plain 5 V charger it is inferred from a sustained rise in
// voltage and shows up about five minutes after plugging in.
#include <Arduino.h>

namespace battery {

struct State {
  bool present = false;   // false: no pack, or the power switch is off
  bool charging = false;  // USB host attached, or inferred from the voltage trend
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
