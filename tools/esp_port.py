"""Shared serial helpers: resolve the board port and open it without toggling reset lines."""
import glob
import os

import serial


def find_port() -> str:
    """$ESP_PORT if set, else the first /dev/cu.usbmodem* (ESP32-S3 native USB)."""
    if port := os.environ.get("ESP_PORT"):
        return port
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        raise SystemExit("no /dev/cu.usbmodem* found; plug the board in or set ESP_PORT")
    return ports[0]


def open_port(timeout: float = 0.3) -> serial.Serial:
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = find_port(), 115200, timeout
    s.dtr = s.rts = False
    s.open()
    return s
