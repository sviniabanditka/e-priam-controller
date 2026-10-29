"""BLE connection lifecycle for the Cybex e-Priam stroller.

Owns the BleakClient, runs the notify pump, parses raw frames into typed
state, and emits events to a PriamObserver. The TUI implements the
observer protocol; this module has no UI dependency.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Optional, Protocol

from bleak import BleakClient

from priam_ble import (
    DRIVE_NAMES,
    DRIVE_UUID,
    INTENSITY_NAMES,
    ROCKING_UUID,
    STATUS_UUID,
    BatteryState,
    RockingState,
    build_drive_cmd,
    build_rocking_cmd,
    find_stroller,
    parse_battery,
    parse_rocking,
)


class ConnectionStatus(str, Enum):
    DISCONNECTED = "disconnected"
    SEARCHING = "searching"
    CONNECTED = "connected"


class PriamObserver(Protocol):
    """UI-side hooks. Methods are called from the BLE worker task."""

    def on_battery(self, state: BatteryState) -> None: ...
    def on_rocking(self, state: RockingState) -> None: ...
    def on_drive(self, level: int) -> None: ...
    def on_status(self, status: ConnectionStatus) -> None: ...
    def on_auto_renew(self, active: bool) -> None: ...
    def on_log(self, msg: str) -> None: ...
    def on_raw(self, label: str, hex_data: str) -> None: ...


# When time_left drops below this, the client re-issues the rocking
# preset (if auto-renew was requested for the current session).
AUTO_RENEW_THRESHOLD_SEC = 60


class PriamClient:
    def __init__(self, observer: PriamObserver) -> None:
        self._obs = observer
        self._bleak: Optional[BleakClient] = None
        # Auto-renew survives reconnects: it's session-scoped, not
        # connection-scoped, so it stays out of _reset_diff_state.
        self._auto_renew_preset: Optional[tuple[int, int, bool]] = None
        self._renew_in_flight: bool = False
        self._reset_diff_state()

    @property
    def is_connected(self) -> bool:
        return self._bleak is not None and self._bleak.is_connected

    # ---------- lifecycle ----------

    async def run(self) -> None:
        """Connect, pump notifications, exit on disconnect or cancellation."""
        self._reset_diff_state()
        self._obs.on_status(ConnectionStatus.SEARCHING)
        try:
            self._obs.on_log("🔍 Searching for stroller...")
            device = await find_stroller(on_progress=lambda _: None)
            if device is None:
                self._obs.on_log("[red]❌ Stroller not found[/red]")
                self._obs.on_status(ConnectionStatus.DISCONNECTED)
                return

            self._notify_device(device.name or "?")
            self._obs.on_log(
                f"📡 Connecting to {device.name or '?'} ({device.address})..."
            )
            async with BleakClient(device.address) as bleak:
                if not bleak.is_connected:
                    self._obs.on_log("[red]❌ Connect failed[/red]")
                    self._obs.on_status(ConnectionStatus.DISCONNECTED)
                    return

                self._bleak = bleak
                self._obs.on_status(ConnectionStatus.CONNECTED)
                self._obs.on_log("[green]✅ Connected[/green]")

                await self._setup_notifications(bleak)
                self._obs.on_log("📡 Subscribed to notifications")

                while bleak.is_connected:
                    await asyncio.sleep(1)

                self._obs.on_status(ConnectionStatus.DISCONNECTED)
                self._obs.on_log("[red]🔌 Disconnected[/red]")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._obs.on_log(f"[red][ERROR] {e!r}[/red]")
            self._obs.on_status(ConnectionStatus.DISCONNECTED)
        finally:
            self._bleak = None

    def _notify_device(self, name: str) -> None:
        # Optional observer hook; older observers without it still work.
        notify = getattr(self._obs, "on_device", None)
        if notify is not None:
            notify(name)

    def _reset_diff_state(self) -> None:
        self._last_battery_bucket: Optional[int] = None
        self._last_intensity: Optional[int] = None
        self._last_set_time_min: Optional[int] = None
        self._last_error: str = "none"

    async def _setup_notifications(self, bleak: BleakClient) -> None:
        async def on_status_n(_, data: bytearray):
            self._handle_battery(bytes(data))

        async def on_drive_n(_, data: bytearray):
            self._handle_drive(bytes(data))

        async def on_rocking_n(_, data: bytearray):
            self._handle_rocking(bytes(data))

        await bleak.start_notify(STATUS_UUID, on_status_n)
        await bleak.start_notify(DRIVE_UUID, on_drive_n)
        await bleak.start_notify(ROCKING_UUID, on_rocking_n)

        # BLE notify only fires on value change; without an initial read the
        # panel stays empty until the device next pushes (battery is slow).
        for uuid, handler, label in (
            (STATUS_UUID, self._handle_battery, "STATUS"),
            (DRIVE_UUID, self._handle_drive, "DRIVE"),
            (ROCKING_UUID, self._handle_rocking, "ROCKING"),
        ):
            try:
                data = bytes(await bleak.read_gatt_char(uuid))
                if data:
                    handler(data)
            except Exception as e:
                self._obs.on_log(
                    f"[yellow]⚠ initial read {label} failed: {e!r}[/yellow]"
                )

    # ---------- notify handlers ----------

    def _handle_battery(self, data: bytes) -> None:
        st = parse_battery(data)
        self._obs.on_battery(st)
        bucket = (st.percent // 5) * 5
        if self._last_battery_bucket != bucket:
            self._last_battery_bucket = bucket
            self._obs.on_log(f"🔋 Battery: {st.percent}% ({st.mv} mV)")
        self._obs.on_raw("STATUS", st.raw_hex)

    def _handle_drive(self, data: bytes) -> None:
        if len(data) >= 1 and data[0] in DRIVE_NAMES:
            self._obs.on_drive(data[0])
        self._obs.on_log(f"📡 Drive notify: {data.hex()}")
        self._obs.on_raw("DRIVE", data.hex())

    def _handle_rocking(self, data: bytes) -> None:
        st = parse_rocking(data)
        if st is None:
            self._obs.on_log(f"[yellow]⚠ Rocking short pkt: {data.hex()}[/yellow]")
            self._obs.on_raw("ROCKING", data.hex())
            return

        self._obs.on_rocking(st)
        self._maybe_auto_renew(st)

        if self._last_intensity is not None and self._last_intensity != st.intensity:
            prev = INTENSITY_NAMES.get(self._last_intensity, "?")
            self._obs.on_log(f"🌀 Intensity changed: {prev} → {st.intensity_name}")
        self._last_intensity = st.intensity

        if (
            self._last_set_time_min is not None
            and self._last_set_time_min != st.set_time_min
        ):
            self._obs.on_log(
                f"⏱  Set time changed: "
                f"{self._last_set_time_min} → {st.set_time_min} min"
            )
        self._last_set_time_min = st.set_time_min

        if st.error != "none" and st.error != self._last_error:
            self._obs.on_log(f"[red]⚠ Rocking error: {st.error}[/red]")
        self._last_error = st.error

        self._obs.on_raw("ROCKING", st.raw_hex)

    # ---------- auto-renew ----------

    def _maybe_auto_renew(self, st: RockingState) -> None:
        if self._auto_renew_preset is None:
            return
        # If the firmware says rocking is OFF (user hit Stop, time ran out
        # without us catching it, etc.), drop the preset — we don't want
        # auto-renew to silently restart a stopped session.
        if st.intensity == 0:
            self._auto_renew_preset = None
            self._renew_in_flight = False
            self._obs.on_auto_renew(False)
            self._obs.on_log("🔁 Auto-renew disabled (rocking stopped)")
            return
        # Once time_left rises again (the firmware accepted our last
        # re-issue), arm the trigger for the next cycle.
        if st.time_left_sec > AUTO_RENEW_THRESHOLD_SEC:
            self._renew_in_flight = False
            return
        if self._renew_in_flight:
            return
        self._renew_in_flight = True
        intensity, duration_min, cont = self._auto_renew_preset
        self._obs.on_log(
            f"🔁 Auto-renew triggered (≤{AUTO_RENEW_THRESHOLD_SEC}s left)"
        )
        asyncio.create_task(self._renew_now(intensity, duration_min, cont))

    async def _renew_now(self, intensity: int, duration_min: int, cont: bool) -> None:
        # Re-issue silently keeps the preset (auto_renew=True).
        await self.write_rocking(intensity, duration_min, cont, auto_renew=True)

    # ---------- write commands ----------

    def _require_connected(self) -> bool:
        if not self.is_connected:
            self._obs.on_log("[yellow]⚠ Not connected — command ignored[/yellow]")
            return False
        return True

    async def write_rocking(
        self,
        intensity: int,
        duration_min: int,
        cont: bool,
        auto_renew: bool = False,
    ) -> None:
        if not self._require_connected():
            return
        try:
            cmd = build_rocking_cmd(intensity, duration_min, cont)
        except ValueError as e:
            self._obs.on_log(f"[red]⚠ {e}[/red]")
            return
        try:
            await self._bleak.write_gatt_char(ROCKING_UUID, cmd)
        except Exception as e:
            self._obs.on_log(f"[red][ERROR] write rocking: {e!r}[/red]")
            return

        self._update_auto_renew_preset(intensity, duration_min, cont, auto_renew)

        if intensity == 0:
            self._obs.on_log(f"⏹ Rocking stopped  hex={cmd.hex()}")
        else:
            renew_tag = "  auto-renew" if auto_renew else ""
            self._obs.on_log(
                f"🌀 Rocking: {INTENSITY_NAMES[intensity]} {duration_min} min "
                f"cont={cont}{renew_tag}  hex={cmd.hex()}"
            )

    def _update_auto_renew_preset(
        self, intensity: int, duration_min: int, cont: bool, auto_renew: bool
    ) -> None:
        prev_active = self._auto_renew_preset is not None
        if auto_renew and intensity != 0:
            self._auto_renew_preset = (intensity, duration_min, cont)
        else:
            self._auto_renew_preset = None
            self._renew_in_flight = False
        new_active = self._auto_renew_preset is not None
        if prev_active != new_active:
            self._obs.on_auto_renew(new_active)

    async def stop_rocking(self) -> None:
        await self.write_rocking(0, 0, False, auto_renew=False)

    async def write_drive(self, level: int) -> None:
        if not self._require_connected():
            return
        try:
            cmd = build_drive_cmd(level)
        except ValueError as e:
            self._obs.on_log(f"[red]⚠ {e}[/red]")
            return
        try:
            await self._bleak.write_gatt_char(DRIVE_UUID, cmd)
        except Exception as e:
            self._obs.on_log(f"[red][ERROR] write drive: {e!r}[/red]")
            return
        self._obs.on_log(f"🚗 Drive: {DRIVE_NAMES[level]}  hex={cmd.hex()}")
