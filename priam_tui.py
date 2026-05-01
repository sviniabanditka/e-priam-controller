"""Textual TUI for the Cybex e-Priam BLE controller.

Implements PriamObserver: PriamClient pushes events here, the App
reflects them in reactive state and the log panel.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.validation import Integer
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    RadioButton,
    RadioSet,
    RichLog,
    Static,
)

from priam_ble import (
    DRIVE_NAMES,
    INTENSITY_NAMES,
    MAX_DURATION_MIN,
    BatteryState,
    RockingState,
)
from priam_client import ConnectionStatus, PriamClient


_CONN_COLOR = {
    ConnectionStatus.CONNECTED: "green",
    ConnectionStatus.SEARCHING: "yellow",
    ConnectionStatus.DISCONNECTED: "red",
}


class StatusPanel(Static):
    """Live state display: battery, rocking, drive, errors."""

    battery_pct: reactive[Optional[int]] = reactive(None)
    battery_mv: reactive[Optional[int]] = reactive(None)
    intensity_name: reactive[str] = reactive("—")
    time_left_min: reactive[Optional[int]] = reactive(None)
    set_time_min: reactive[Optional[int]] = reactive(None)
    cont_on_disc: reactive[Optional[bool]] = reactive(None)
    auto_renew_active: reactive[Optional[bool]] = reactive(None)
    rocking_error: reactive[str] = reactive("—")
    drive_mode: reactive[str] = reactive("—")
    connection: reactive[ConnectionStatus] = reactive(ConnectionStatus.DISCONNECTED)

    _LINE_IDS = (
        "conn_line",
        "battery_line",
        "rocking_line",
        "timer_line",
        "cont_line",
        "auto_renew_line",
        "error_line",
        "drive_line",
    )

    def compose(self) -> ComposeResult:
        yield Label("Status", classes="section_title")
        for line_id in self._LINE_IDS:
            yield Label("", id=line_id, classes="status_line")

    def on_mount(self) -> None:
        self._lines = {
            line_id: self.query_one(f"#{line_id}", Label)
            for line_id in self._LINE_IDS
        }
        self._refresh()

    def watch_connection(self) -> None: self._refresh()
    def watch_battery_pct(self) -> None: self._refresh()
    def watch_battery_mv(self) -> None: self._refresh()
    def watch_intensity_name(self) -> None: self._refresh()
    def watch_time_left_min(self) -> None: self._refresh()
    def watch_set_time_min(self) -> None: self._refresh()
    def watch_cont_on_disc(self) -> None: self._refresh()
    def watch_auto_renew_active(self) -> None: self._refresh()
    def watch_rocking_error(self) -> None: self._refresh()
    def watch_drive_mode(self) -> None: self._refresh()

    def _refresh(self) -> None:
        if not hasattr(self, "_lines"):
            return  # reactive watchers can fire before on_mount

        color = _CONN_COLOR.get(self.connection, "white")
        self._lines["conn_line"].update(
            f"🔌 [b {color}]{self.connection.value}[/]"
        )

        if self.battery_pct is None:
            self._lines["battery_line"].update("🔋 —")
        else:
            self._lines["battery_line"].update(
                f"🔋 {self.battery_pct}%  ({self.battery_mv} mV)"
            )

        self._lines["rocking_line"].update(f"🌀 {self.intensity_name}")

        if self.time_left_min is None or self.set_time_min is None:
            self._lines["timer_line"].update("⏱  —")
        else:
            self._lines["timer_line"].update(
                f"⏱  {self.time_left_min} / {self.set_time_min} min"
            )

        if self.cont_on_disc is None:
            self._lines["cont_line"].update("🔁 cont: —")
        else:
            self._lines["cont_line"].update(
                f"🔁 cont: {'yes' if self.cont_on_disc else 'no'}"
            )

        if self.auto_renew_active is None:
            self._lines["auto_renew_line"].update("🔄 auto-renew: —")
        else:
            self._lines["auto_renew_line"].update(
                f"🔄 auto-renew: {'yes' if self.auto_renew_active else 'no'}"
            )

        err_color = "red" if self.rocking_error not in ("—", "none") else "white"
        self._lines["error_line"].update(
            f"⚠  [{err_color}]{self.rocking_error}[/]"
        )

        self._lines["drive_line"].update(f"🚗 {self.drive_mode}")


class ControlsPanel(Static):
    def compose(self) -> ComposeResult:
        yield Label("Controls", classes="section_title")
        yield Label("[b]r[/b] — Rocking…")
        yield Label("[b]c[/b] — Reconnect")
        yield Label("[b]d[/b] — Drive mode…")
        yield Label("[b]s[/b] — Stop rocking")
        yield Label("[b]q[/b] — Quit")


class RockingScreen(ModalScreen[Optional[tuple[int, int, bool, bool]]]):
    """Pick intensity / duration / continue-on-disconnect / auto-renew."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    # RadioSet index → intensity protocol value
    _INTENSITY_BY_INDEX = (1, 2, 3, 0)  # HIGH, MED, LOW, OFF

    def __init__(
        self,
        current_intensity: int = 2,
        current_duration_min: int = 30,
        current_cont: bool = False,
        current_auto_renew: bool = False,
    ) -> None:
        super().__init__()
        self._cur_intensity = (
            current_intensity if current_intensity in self._INTENSITY_BY_INDEX else 2
        )
        self._cur_duration = current_duration_min if current_duration_min > 0 else 30
        self._cur_cont = current_cont
        self._cur_auto_renew = current_auto_renew

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Rocking", id="dialog_title")
            yield Label("Intensity:")
            with RadioSet(id="intensity"):
                yield RadioButton("HIGH", value=(self._cur_intensity == 1))
                yield RadioButton("MED", value=(self._cur_intensity == 2))
                yield RadioButton("LOW", value=(self._cur_intensity == 3))
                yield RadioButton("OFF", value=(self._cur_intensity == 0))
            yield Label(f"Duration (minutes, 1–{MAX_DURATION_MIN}):")
            yield Input(
                value=str(self._cur_duration),
                id="duration",
                validators=[Integer(minimum=1, maximum=MAX_DURATION_MIN)],
            )
            yield Checkbox("Continue on disconnect", value=self._cur_cont, id="cont")
            yield Checkbox(
                "Auto-renew (re-issue when ≤1 min left)",
                value=self._cur_auto_renew,
                id="auto_renew",
            )
            with Horizontal(id="dialog_buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Apply", id="apply", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return

        idx = self.query_one("#intensity", RadioSet).pressed_index
        if not 0 <= idx < len(self._INTENSITY_BY_INDEX):
            self.app.bell()
            return
        intensity = self._INTENSITY_BY_INDEX[idx]

        try:
            duration = int(self.query_one("#duration", Input).value)
        except ValueError:
            self.app.bell()
            return
        if intensity != 0 and not (1 <= duration <= MAX_DURATION_MIN):
            self.app.bell()
            return

        cont = self.query_one("#cont", Checkbox).value
        auto_renew = self.query_one("#auto_renew", Checkbox).value
        self.dismiss((intensity, duration, cont, auto_renew))

    def action_cancel(self) -> None:
        self.dismiss(None)


class DriveScreen(ModalScreen[Optional[int]]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Drive mode", id="dialog_title")
            with RadioSet(id="level"):
                yield RadioButton("ECO", value=True)
                yield RadioButton("TOUR")
                yield RadioButton("BOOST (unofficial)")
            with Horizontal(id="dialog_buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Apply", id="apply", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        idx = self.query_one("#level", RadioSet).pressed_index
        # ECO=1, TOUR=2, BOOST=3
        self.dismiss(idx + 1 if idx in (0, 1, 2) else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PriamApp(App):
    """Top-level TUI app. Implements PriamObserver."""

    CSS_PATH = "priam_styles.tcss"
    TITLE = "Cybex e-Priam"

    BINDINGS = [
        Binding("r", "rocking", "Rocking"),
        Binding("c", "reconnect", "Reconnect"),
        Binding("d", "drive", "Drive"),
        Binding("s", "stop_rocking", "Stop"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    _NAME_TO_INTENSITY = {"HIGH": 1, "MED": 2, "LOW": 3, "OFF": 0}

    def __init__(self, log_file: Optional[Path] = None) -> None:
        super().__init__()
        self._log_path = log_file
        self._log_fh = None
        self._client = PriamClient(observer=self)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield StatusPanel(id="status_panel")
                yield ControlsPanel(id="controls_panel")
            yield RichLog(id="log_panel", highlight=True, markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        if self._log_path:
            self._log_fh = open(self._log_path, "a", buffering=1)
        self._start_connect_worker()

    def on_unmount(self) -> None:
        if self._log_fh:
            self._log_fh.close()

    def _start_connect_worker(self) -> None:
        # exclusive=True cancels any prior connect worker; the cancelled
        # task's `async with BleakClient(...)` tears down the existing
        # connection cleanly.
        self.run_worker(self._client.run(), exclusive=True, group="connect")

    # ---------- PriamObserver ----------

    def on_battery(self, st: BatteryState) -> None:
        sp = self.query_one(StatusPanel)
        sp.battery_pct = st.percent
        sp.battery_mv = st.mv

    def on_rocking(self, st: RockingState) -> None:
        sp = self.query_one(StatusPanel)
        sp.intensity_name = st.intensity_name
        sp.time_left_min = st.time_left_min
        sp.set_time_min = st.set_time_min
        sp.cont_on_disc = st.disc
        sp.rocking_error = st.error

    def on_drive(self, level: int) -> None:
        self.query_one(StatusPanel).drive_mode = DRIVE_NAMES.get(level, "—")

    def on_status(self, status: ConnectionStatus) -> None:
        self.query_one(StatusPanel).connection = status

    def on_auto_renew(self, active: bool) -> None:
        self.query_one(StatusPanel).auto_renew_active = active

    def on_log(self, msg: str) -> None:
        ts = self._now()
        try:
            self.query_one("#log_panel", RichLog).write(f"[dim]{ts}[/dim] {msg}")
        except Exception:
            pass
        if self._log_fh:
            self._log_fh.write(f"[{ts}] {_strip_markup(msg)}\n")

    def on_raw(self, label: str, hex_data: str) -> None:
        if self._log_fh:
            self._log_fh.write(f"[{self._now()}] {label} {hex_data}\n")

    @staticmethod
    def _now() -> str:
        return datetime.datetime.now().strftime("%H:%M:%S")

    # ---------- actions ----------

    def action_rocking(self) -> None:
        # push_screen_wait requires worker context in Textual 8.x.
        self.run_worker(self._do_rocking(), group="dialog")

    async def _do_rocking(self) -> None:
        sp = self.query_one(StatusPanel)
        result = await self.push_screen_wait(
            RockingScreen(
                current_intensity=self._NAME_TO_INTENSITY.get(sp.intensity_name, 2),
                current_duration_min=sp.set_time_min or 30,
                current_cont=bool(sp.cont_on_disc),
                current_auto_renew=bool(sp.auto_renew_active),
            )
        )
        if result is None:
            return
        intensity, duration_min, cont, auto_renew = result
        await self._client.write_rocking(intensity, duration_min, cont, auto_renew)

    def action_drive(self) -> None:
        self.run_worker(self._do_drive(), group="dialog")

    async def _do_drive(self) -> None:
        result = await self.push_screen_wait(DriveScreen())
        if result is None:
            return
        await self._client.write_drive(result)

    async def action_stop_rocking(self) -> None:
        await self._client.stop_rocking()

    def action_reconnect(self) -> None:
        self.on_log("🔄 Reconnect requested")
        self._start_connect_worker()


def _strip_markup(s: str) -> str:
    """Drop Rich-style `[tag]` segments for plain-text file logging."""
    out = []
    depth = 0
    for ch in s:
        if ch == "[":
            depth += 1
            continue
        if ch == "]" and depth:
            depth -= 1
            continue
        if depth == 0:
            out.append(ch)
    return "".join(out)
