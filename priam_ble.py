"""BLE protocol layer for Cybex e-Priam stroller.

Pure functions and dataclasses — no UI, no I/O side effects (other than the
Bleak scanner in `find_stroller`). The TUI imports from here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

STATUS_UUID = "a1fc0102-78d3-40c2-9b6f-3c5f7b2797df"
DRIVE_UUID = "a1fc0103-78d3-40c2-9b6f-3c5f7b2797df"
ROCKING_UUID = "a1fc0104-78d3-40c2-9b6f-3c5f7b2797df"

MANUFACTURER_ID = 1933

INTENSITY_NAMES = {0: "OFF", 1: "HIGH", 2: "MED", 3: "LOW"}
DRIVE_NAMES = {1: "ECO", 2: "TOUR", 3: "BOOST"}

# Empirical firmware limit: time_left register clamps at ~30410 sec
# (≈ 506 min). Anything above wraps modulo 30410 — set_time still echoes
# the larger value, but the live countdown is bogus. Stay below the wrap
# point with a small safety margin.
MAX_DURATION_MIN = 500


@dataclass
class BatteryState:
    percent: int
    mv: int
    raw_hex: str


@dataclass
class RockingState:
    intensity: int
    intensity_name: str
    time_left_min: int
    set_time_min: int
    time_left_sec: int
    set_time_sec: int
    disc: bool
    error: str
    raw_hex: str


def parse_battery(data: bytes) -> BatteryState:
    voltage = (data[3] & 0xFF) * 2
    pct = max(0, min(100, (voltage - 315) / (380 - 315) * 100))
    return BatteryState(percent=round(pct), mv=voltage * 10, raw_hex=data.hex())


def parse_rocking(data: bytes) -> RockingState | None:
    if len(data) < 5:
        return None
    intensity = data[0] & 0x0F
    disc = bool(data[0] & 0x10)
    error = "brake not engaged" if (data[0] ^ 0x80) == 20 else "none"
    time_left_sec = (data[1] & 0xFF) | ((data[2] & 0xFF) << 8)
    set_time_sec = (data[3] & 0xFF) | ((data[4] & 0xFF) << 8)
    return RockingState(
        intensity=intensity,
        intensity_name=INTENSITY_NAMES.get(intensity, f"?{intensity}"),
        time_left_min=round(time_left_sec / 60),
        set_time_min=round(set_time_sec / 60),
        time_left_sec=time_left_sec,
        set_time_sec=set_time_sec,
        disc=disc,
        error=error,
        raw_hex=data.hex(),
    )


def build_rocking_cmd(intensity: int, duration_minutes: int, continue_on_disconnect: bool) -> bytes:
    if intensity not in (0, 1, 2, 3):
        raise ValueError(f"intensity must be 0..3, got {intensity}")
    if intensity == 0:
        return bytes([0, 0, 0, 0, 0])
    if not (1 <= duration_minutes <= MAX_DURATION_MIN):
        raise ValueError(f"duration must be 1..{MAX_DURATION_MIN} minutes, got {duration_minutes}")
    duration_sec = duration_minutes * 60
    d_byte = intensity | (0x10 if continue_on_disconnect else 0)
    lo = duration_sec & 0xFF
    hi = (duration_sec >> 8) & 0xFF
    # Mirror the 5-byte status layout: [flags, time_left_lo, time_left_hi,
    # set_time_lo, set_time_hi]. Setting time_left == set_time forces the
    # firmware to start the countdown from the full duration, instead of
    # carrying over the previous time_left when re-issuing while active.
    return bytes([d_byte, lo, hi, lo, hi])


def build_drive_cmd(level: int) -> bytes:
    if level not in (1, 2, 3):
        raise ValueError(f"drive level must be 1..3, got {level}")
    return bytes([level])


async def find_stroller(
    timeout: float = 15.0,
    on_progress: Callable[[str], None] | None = None,
) -> BLEDevice | None:
    found: BLEDevice | None = None

    def on_detect(device: BLEDevice, adv: AdvertisementData):
        nonlocal found
        if found:
            return
        if adv.manufacturer_data.get(MANUFACTURER_ID) or (
            device.name and "priam" in device.name.lower()
        ):
            found = device

    if on_progress:
        on_progress("🔍 Searching for stroller...")

    async with BleakScanner(detection_callback=on_detect):
        for _ in range(int(timeout)):
            await asyncio.sleep(1)
            if found:
                break

    return found
