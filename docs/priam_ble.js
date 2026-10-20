// Pure protocol layer — JS port of priam_ble.py. No DOM, no Bluetooth.

export const STATUS_UUID = "a1fc0102-78d3-40c2-9b6f-3c5f7b2797df";
export const DRIVE_UUID = "a1fc0103-78d3-40c2-9b6f-3c5f7b2797df";
export const ROCKING_UUID = "a1fc0104-78d3-40c2-9b6f-3c5f7b2797df";

// The GATT service that contains the three characteristics. Unknown yet —
// bleak addresses characteristics directly so the Python app never needed it.
// Web Bluetooth requires it in optionalServices, so we try the likely
// candidates and pick whichever actually holds ROCKING_UUID at runtime.
export const SERVICE_UUID_CANDIDATES = [
  "a1fc0101-78d3-40c2-9b6f-3c5f7b2797df",
  "a1fc0100-78d3-40c2-9b6f-3c5f7b2797df",
];

export const INTENSITY_NAMES = { 0: "OFF", 1: "HIGH", 2: "MED", 3: "LOW" };
export const DRIVE_NAMES = { 1: "ECO", 2: "TOUR", 3: "BOOST" };
export const MAX_DURATION_MIN = 500;

export const hex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, "0")).join("");

export function parseBattery(data) {
  const voltage = (data[3] & 0xff) * 2;
  const pct = Math.max(0, Math.min(100, ((voltage - 315) / (380 - 315)) * 100));
  return { percent: Math.round(pct), mv: voltage * 10, rawHex: hex(data) };
}

export function parseRocking(data) {
  if (data.length < 5) return null;
  const intensity = data[0] & 0x0f;
  const timeLeftSec = data[1] | (data[2] << 8);
  const setTimeSec = data[3] | (data[4] << 8);
  return {
    intensity,
    intensityName: INTENSITY_NAMES[intensity] ?? `?${intensity}`,
    timeLeftSec,
    setTimeSec,
    timeLeftMin: Math.round(timeLeftSec / 60),
    setTimeMin: Math.round(setTimeSec / 60),
    disc: Boolean(data[0] & 0x10),
    error: (data[0] ^ 0x80) === 20 ? "brake not engaged" : "none",
    rawHex: hex(data),
  };
}

export function buildRockingCmd(intensity, durationMin, cont) {
  if (![0, 1, 2, 3].includes(intensity)) throw new Error(`intensity must be 0..3, got ${intensity}`);
  if (intensity === 0) return new Uint8Array(5);
  if (!(durationMin >= 1 && durationMin <= MAX_DURATION_MIN))
    throw new Error(`duration must be 1..${MAX_DURATION_MIN} minutes, got ${durationMin}`);
  const sec = durationMin * 60;
  const lo = sec & 0xff, hi = (sec >> 8) & 0xff;
  // [flags, time_left_lo, time_left_hi, set_time_lo, set_time_hi] — both fields
  // equal so the firmware restarts the countdown instead of keeping old time_left.
  return new Uint8Array([intensity | (cont ? 0x10 : 0), lo, hi, lo, hi]);
}

export function buildDriveCmd(level) {
  if (![1, 2, 3].includes(level)) throw new Error(`drive level must be 1..3, got ${level}`);
  return new Uint8Array([level]);
}
