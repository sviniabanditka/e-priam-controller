"""Textual TUI for the Cybex e-Priam BLE controller.

Implements PriamObserver: PriamClient pushes events here, the App
reflects them in reactive state and the log panel.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.validation import Integer
from textual.widget import Widget
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
    Rule,
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

# The dialog slider works in 10-minute steps; 30 min is the practical floor.
MIN_DURATION_MIN = 30
DURATION_STEP = 10
PRESET_MINUTES = (30, 60, 120, 240, 500)

_CONN_COLOR = {
    ConnectionStatus.CONNECTED: "green",
    ConnectionStatus.SEARCHING: "yellow",
    ConnectionStatus.DISCONNECTED: "red",
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _gauge(ratio: float, width: int, color: str) -> str:
    """Markup gauge: colored fill, dim empty track."""
    ratio = _clamp(ratio, 0.0, 1.0)
    filled = round(ratio * width)
    return f"[{color}]{'█' * filled}[/][dim]{'░' * (width - filled)}[/]"


def _clock_after(minutes: int) -> str:
    return (datetime.datetime.now() + datetime.timedelta(minutes=minutes)).strftime("%H:%M")


def _battery_color(pct: int) -> str:
    if pct >= 50:
        return "green"
    if pct >= 20:
        return "yellow"
    return "red"


class Slider(Widget, can_focus=True):
    """Minimal horizontal slider: keyboard steps + click to jump."""

    COMPONENT_CLASSES = {"slider--track", "slider--filled", "slider--thumb"}
    DEFAULT_CSS = """
    Slider {
        height: 1;
        width: 1fr;
    }
    Slider > .slider--track { color: $text-disabled; }
    Slider > .slider--filled { color: $accent; }
    Slider > .slider--thumb { color: $accent; text-style: bold; }
    Slider:focus > .slider--filled { color: $accent-lighten-2; }
    Slider:focus > .slider--thumb { color: $accent-lighten-2; }
    """

    BINDINGS = [
        Binding("right", "step_up", show=False),
        Binding("+", "step_up", show=False),
        Binding("=", "step_up", show=False),
        Binding("left", "step_down", show=False),
        Binding("-", "step_down", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("home", "to_min", show=False),
        Binding("end", "to_max", show=False),
    ]

    class Changed(Message):
        """Posted when the value changes."""

        def __init__(self, value: int) -> None:
            self.value = value
            super().__init__()

    value: reactive[int] = reactive(0)

    def __init__(
        self,
        minimum: int = 0,
        maximum: int = 100,
        step: int = 1,
        value: int | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self.minimum = int(minimum)
        self.maximum = int(maximum)
        self.step = max(1, int(step))
        self.value = self._snap(
            self.minimum if value is None else int(value)
        )

    # -- value helpers -------------------------------------------------
    def _snap(self, value: int) -> int:
        stepped = round((value - self.minimum) / self.step) * self.step + self.minimum
        return int(_clamp(stepped, self.minimum, self.maximum))

    def validate_value(self, value: int) -> int:
        return int(_clamp(value, self.minimum, self.maximum))

    def watch_value(self, value: int) -> None:
        self.refresh()
        if self.is_mounted:
            self.post_message(self.Changed(value))

    def watch_has_focus(self) -> None:
        self.refresh()

    # -- rendering -----------------------------------------------------
    def render(self) -> Text:
        width = max(6, self.content_size.width)
        span = max(1, self.maximum - self.minimum)
        ratio = (self.value - self.minimum) / span
        thumb = int(round(ratio * (width - 1)))
        text = Text(no_wrap=True, overflow="crop")
        text.append("━" * thumb, style=self.get_component_rich_style("slider--filled"))
        text.append("●", style=self.get_component_rich_style("slider--thumb"))
        text.append(
            "─" * max(0, width - thumb - 1),
            style=self.get_component_rich_style("slider--track"),
        )
        return text

    # -- interaction ---------------------------------------------------
    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.focus()
        width = max(6, self.content_size.width)
        x = int(_clamp(event.x, 0, width - 1))
        ratio = x / max(1, width - 1)
        self.value = self._snap(
            self.minimum + round(ratio * (self.maximum - self.minimum))
        )

    def action_step_up(self) -> None:
        self.value = min(self.maximum, self.value + self.step)

    def action_step_down(self) -> None:
        self.value = max(self.minimum, self.value - self.step)

    def action_page_up(self) -> None:
        self.value = min(self.maximum, self.value + self.step * 10)

    def action_page_down(self) -> None:
        self.value = max(self.minimum, self.value - self.step * 10)

    def action_to_min(self) -> None:
        self.value = self.minimum

    def action_to_max(self) -> None:
        self.value = self.maximum


class StatusPanel(Static):
    """Live state display: connection, battery, rocking, drive."""

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
    device_name: reactive[str] = reactive("")

    _ROWS = (
        ("Link", "conn_line"),
        ("Battery", "battery_line"),
        ("Rocking", "rocking_line"),
        ("Session", "timer_line"),
        ("After drop", "cont_line"),
        ("Auto-renew", "auto_renew_line"),
        ("Error", "error_line"),
        ("Drive", "drive_line"),
    )
    _LINE_IDS = tuple(line_id for _, line_id in _ROWS)

    def compose(self) -> ComposeResult:
        with Grid(id="status_grid"):
            for label, line_id in self._ROWS:
                yield Label(label, classes="status_key")
                yield Label("", id=line_id, classes="status_val")

    def on_mount(self) -> None:
        self.border_title = "Status"
        self._lines = {
            line_id: self.query_one(f"#{line_id}", Label)
            for line_id in self._LINE_IDS
        }
        self._refresh()

    def on_resize(self, event: events.Resize) -> None:
        # Two stat pairs per row on wide terminals, one pair on narrow ones.
        try:
            grid = self.query_one("#status_grid")
            grid.set_class(event.size.width < 118, "narrow")
        except Exception:
            pass

    def watch_connection(self) -> None: self._refresh()
    def watch_device_name(self) -> None: self._refresh()
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

        lines = self._lines

        color = _CONN_COLOR.get(self.connection, "white")
        device = f"  [dim]· {self.device_name}[/dim]" if self.device_name else ""
        lines["conn_line"].update(
            f"[{color}]●[/] [b {color}]{self.connection.value}[/]{device}"
        )

        if self.battery_pct is None:
            lines["battery_line"].update("[dim]—[/dim]")
        else:
            bcolor = _battery_color(self.battery_pct)
            mv = self.battery_mv or 0
            lines["battery_line"].update(
                f"{_gauge(self.battery_pct / 100, 10, bcolor)} "
                f"[b]{self.battery_pct}%[/] [dim]{mv / 1000:.2f} V[/]"
            )

        if self.intensity_name in ("—", "OFF"):
            lines["rocking_line"].update(
                f"[dim]{self.intensity_name} · idle[/dim]"
            )
        else:
            set_txt = (
                f" [dim]· {self.set_time_min} min[/]" if self.set_time_min else ""
            )
            lines["rocking_line"].update(
                f"[b cyan]{self.intensity_name}[/]{set_txt}"
            )

        if (
            self.time_left_min is None
            or self.set_time_min is None
            or self.set_time_min <= 0
            or self.intensity_name in ("—", "OFF")
        ):
            lines["timer_line"].update("[dim]no active session[/dim]")
        else:
            ratio = self.time_left_min / self.set_time_min
            lines["timer_line"].update(
                f"{_gauge(ratio, 10, 'cyan')} "
                f"[b]{self.time_left_min}[/] [dim]min left · ends "
                f"{_clock_after(self.time_left_min)}[/]"
            )

        if self.cont_on_disc is None:
            lines["cont_line"].update("[dim]—[/dim]")
        else:
            lines["cont_line"].update(
                "[green]keep rocking[/]" if self.cont_on_disc else "[dim]stop[/]"
            )

        if self.auto_renew_active is None:
            lines["auto_renew_line"].update("[dim]—[/dim]")
        else:
            lines["auto_renew_line"].update(
                "[green]on[/]" if self.auto_renew_active else "[dim]off[/]"
            )

        if self.rocking_error in ("—", "none"):
            lines["error_line"].update("[dim]none[/dim]")
        else:
            lines["error_line"].update(f"[b red]⚠ {self.rocking_error}[/]")

        lines["drive_line"].update(
            f"[b]{self.drive_mode}[/]" if self.drive_mode not in ("—", "") else "[dim]—[/dim]"
        )


class ControlsPanel(Static):
    _KEYS = (
        ("r", "Rocking…"),
        ("d", "Drive mode…"),
        ("s", "Stop rocking"),
        ("c", "Reconnect"),
        ("q", "Quit"),
    )

    def compose(self) -> ComposeResult:
        with Grid(id="keys_grid"):
            for key, desc in self._KEYS:
                yield Label(key, classes="key")
                yield Label(desc, classes="key_desc")
        yield Label(
            "←/→ step · PgUp/PgDn jump\nor click the slider",
            classes="panel_hint",
        )

    def on_mount(self) -> None:
        self.border_title = "Keys"


class PresetButton(Button):
    """Preset shortcut: clickable, but skipped by keyboard focus."""

    can_focus = False


class RockingScreen(ModalScreen[Optional[tuple[int, int, bool, bool]]]):
    """Pick intensity / duration / continue-on-disconnect / auto-renew."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    AUTO_FOCUS = "#duration_slider"

    # RadioSet index → intensity protocol value
    _INTENSITY_BY_INDEX = (1, 2, 3, 0)  # HIGH, MED, LOW, OFF

    def __init__(
        self,
        current_intensity: int = 2,
        current_duration_min: int = 60,
        current_cont: bool = False,
        current_auto_renew: bool = False,
        current_left_min: Optional[int] = None,
    ) -> None:
        super().__init__()
        self._cur_intensity = (
            current_intensity if current_intensity in self._INTENSITY_BY_INDEX else 2
        )
        self._cur_duration = int(
            _clamp(current_duration_min or MIN_DURATION_MIN, MIN_DURATION_MIN, MAX_DURATION_MIN)
        )
        self._cur_cont = current_cont
        self._cur_auto_renew = current_auto_renew
        self._cur_left = current_left_min
        self._syncing = False

    # -- layout --------------------------------------------------------
    def compose(self) -> ComposeResult:
        dur = self._cur_duration
        with Vertical(id="dialog"):
            yield Label("🌀  Rocking", classes="dialog_title")
            if self._cur_left is not None and self._cur_intensity != 0:
                yield Label(
                    f"Now: {INTENSITY_NAMES.get(self._cur_intensity, '?')}"
                    f" · {self._cur_left} min left",
                    classes="dialog_note",
                )
            yield Label("Intensity", classes="field_label")
            yield RadioSet(
                RadioButton("HIGH", value=(self._cur_intensity == 1)),
                RadioButton("MED", value=(self._cur_intensity == 2)),
                RadioButton("LOW", value=(self._cur_intensity == 3)),
                RadioButton("OFF", value=(self._cur_intensity == 0)),
                id="intensity",
                compact=True,
            )
            yield Rule(classes="dialog_rule")

            with Horizontal(classes="field_row"):
                yield Label("Duration", classes="field_label")
                yield Label("", id="duration_value", classes="field_value")
                yield Label("", id="duration_eta", classes="field_eta")

            yield Slider(
                MIN_DURATION_MIN,
                MAX_DURATION_MIN,
                DURATION_STEP,
                value=dur,
                id="duration_slider",
            )

            with Horizontal(classes="duration_row"):
                yield Input(
                    value=str(dur),
                    id="duration",
                    type="integer",
                    validators=[Integer(minimum=MIN_DURATION_MIN, maximum=MAX_DURATION_MIN)],
                    select_on_focus=True,
                )
                yield Label("min", classes="unit")
                yield Label("quick", classes="unit")
                for minutes in PRESET_MINUTES:
                    yield PresetButton(
                        str(minutes),
                        id=f"preset_{minutes}",
                        classes="preset",
                    )

            yield Label("", id="hint", classes="hint")
            yield Checkbox(
                "Continue rocking after disconnect",
                value=self._cur_cont,
                id="cont",
            )
            yield Checkbox(
                "Auto-renew when ≤1 min left",
                value=self._cur_auto_renew,
                id="auto_renew",
            )
            with Horizontal(id="dialog_buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Apply", id="apply", variant="primary")

    def on_mount(self) -> None:
        self._update_labels()
        self._update_enabled_state()

    # -- sync ----------------------------------------------------------
    def _update_labels(self) -> None:
        try:
            slider = self.query_one("#duration_slider", Slider)
            value_label = self.query_one("#duration_value", Label)
            eta_label = self.query_one("#duration_eta", Label)
        except Exception:
            return
        minutes = slider.value
        value_label.update(f"[b]{minutes}[/] min")
        eta_label.update(
            f"[dim]→ ends {_clock_after(minutes)}[/]"
            if self._intensity() != 0
            else "[dim]off[/]"
        )

    def _update_enabled_state(self) -> None:
        off = self._intensity() == 0
        for widget_id in ("#duration_slider", "#duration", "#auto_renew"):
            try:
                self.query_one(widget_id).disabled = off
            except Exception:
                pass
        try:
            self.query_one("#cont").disabled = off
        except Exception:
            pass
        hint = "OFF stops rocking — duration is ignored." if off else ""
        self._set_hint(hint)

    def _set_hint(self, message: str, error: bool = False) -> None:
        try:
            hint = self.query_one("#hint", Label)
        except Exception:
            return
        hint.update(f"[b red]{message}[/]" if error else f"[dim]{message}[/]")
        hint.set_class(error, "error")

    def _intensity(self) -> int:
        try:
            idx = self.query_one("#intensity", RadioSet).pressed_index
        except Exception:
            return self._cur_intensity
        if 0 <= idx < len(self._INTENSITY_BY_INDEX):
            return self._INTENSITY_BY_INDEX[idx]
        return self._cur_intensity

    # -- events --------------------------------------------------------
    def on_slider_changed(self, event: Slider.Changed) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            field = self.query_one("#duration", Input)
            if field.value != str(event.value):
                field.value = str(event.value)
            self._update_labels()
        finally:
            self._syncing = False

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "duration" or self._syncing:
            return
        text = event.value.strip()
        if text.isdigit():
            minutes = int(text)
            if MIN_DURATION_MIN <= minutes <= MAX_DURATION_MIN:
                self._syncing = True
                try:
                    self.query_one("#duration_slider", Slider).value = minutes
                    self._update_labels()
                finally:
                    self._syncing = False

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "duration":
            self._apply()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        self._update_enabled_state()
        self._update_labels()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "cancel":
            self.dismiss(None)
            return
        if button_id == "apply":
            self._apply()
            return
        if button_id.startswith("preset_"):
            minutes = int(button_id.removeprefix("preset_"))
            self.query_one("#duration_slider", Slider).value = minutes
            self.query_one("#duration", Input).value = str(minutes)
            self._update_labels()

    # -- apply ---------------------------------------------------------
    def _apply(self) -> None:
        intensity = self._intensity()
        if intensity == 0:
            self.dismiss((0, 0, False, False))
            return

        try:
            minutes = int(self.query_one("#duration", Input).value)
        except ValueError:
            self._set_hint("Enter a number between 30 and 500.", error=True)
            self.app.bell()
            return
        if not (MIN_DURATION_MIN <= minutes <= MAX_DURATION_MIN):
            self._set_hint("Duration must be between 30 and 500 minutes.", error=True)
            self.app.bell()
            return

        cont = self.query_one("#cont", Checkbox).value
        auto_renew = self.query_one("#auto_renew", Checkbox).value
        self.dismiss((intensity, minutes, cont, auto_renew))

    def action_cancel(self) -> None:
        self.dismiss(None)


class DriveScreen(ModalScreen[Optional[int]]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    _MODES = (
        (1, "ECO", "max range"),
        (2, "TOUR", "balanced"),
        (3, "BOOST", "extra power · unofficial"),
    )

    def __init__(self, current_level: int = 1) -> None:
        super().__init__()
        self._current = current_level if current_level in (1, 2, 3) else 1

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("🚗  Drive mode", classes="dialog_title")
            with RadioSet(id="level"):
                for level, name, desc in self._MODES:
                    yield RadioButton(
                        f"{name} — {desc}", value=(level == self._current)
                    )
            yield Label(
                "BOOST is accepted by firmware but hidden in the official app.",
                classes="dialog_note",
            )
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
    SUB_TITLE = "connecting…"

    BINDINGS = [
        Binding("r", "rocking", "Rocking"),
        Binding("c", "reconnect", "Reconnect"),
        Binding("d", "drive", "Drive"),
        Binding("s", "stop_rocking", "Stop"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    _NAME_TO_INTENSITY = {"HIGH": 1, "MED": 2, "LOW": 3, "OFF": 0}
    _NAME_TO_DRIVE = {name: level for level, name in DRIVE_NAMES.items()}

    def __init__(self, log_file: Optional[Path] = None) -> None:
        super().__init__()
        self._log_path = log_file
        self._log_fh = None
        self._client = PriamClient(observer=self)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="main"):
            yield StatusPanel(id="status_panel")
            with Horizontal(id="bottom"):
                yield ControlsPanel(id="controls_panel")
                yield RichLog(
                    id="log_panel",
                    highlight=True,
                    markup=True,
                    wrap=True,
                    max_lines=5000,
                )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#log_panel", RichLog).border_title = "Event log"
        if self._log_path:
            self._log_fh = open(self._log_path, "a", buffering=1)
        self._sync_header()
        self._start_connect_worker()

    def on_unmount(self) -> None:
        if self._log_fh:
            self._log_fh.close()

    def _start_connect_worker(self) -> None:
        # exclusive=True cancels any prior connect worker; the cancelled
        # task's `async with BleakClient(...)` tears down the existing
        # connection cleanly.
        self.run_worker(self._client.run(), exclusive=True, group="connect")

    # ---------- header ----------

    def _sync_header(self) -> None:
        try:
            sp = self.query_one(StatusPanel)
        except Exception:
            return
        parts = [sp.connection.value]
        if sp.device_name:
            parts.append(sp.device_name)
        if sp.battery_pct is not None:
            parts.append(f"🔋 {sp.battery_pct}%")
        if sp.intensity_name not in ("—", "OFF"):
            timer = f" {sp.time_left_min}/{sp.set_time_min}" if sp.time_left_min else ""
            parts.append(f"🌀 {sp.intensity_name}{timer}")
        if sp.drive_mode not in ("—", ""):
            parts.append(f"🚗 {sp.drive_mode}")
        try:
            self.sub_title = " · ".join(parts)
        except Exception:
            pass

    # ---------- PriamObserver ----------

    def on_device(self, name: str) -> None:
        self.query_one(StatusPanel).device_name = name
        self._sync_header()

    def on_battery(self, st: BatteryState) -> None:
        sp = self.query_one(StatusPanel)
        sp.battery_pct = st.percent
        sp.battery_mv = st.mv
        self._sync_header()

    def on_rocking(self, st: RockingState) -> None:
        sp = self.query_one(StatusPanel)
        sp.intensity_name = st.intensity_name
        sp.time_left_min = st.time_left_min
        sp.set_time_min = st.set_time_min
        sp.cont_on_disc = st.disc
        sp.rocking_error = st.error
        self._sync_header()

    def on_drive(self, level: int) -> None:
        self.query_one(StatusPanel).drive_mode = DRIVE_NAMES.get(level, "—")
        self._sync_header()

    def on_status(self, status: ConnectionStatus) -> None:
        self.query_one(StatusPanel).connection = status
        self._sync_header()

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
                current_duration_min=sp.set_time_min or 60,
                current_cont=bool(sp.cont_on_disc),
                current_auto_renew=bool(sp.auto_renew_active),
                current_left_min=sp.time_left_min,
            )
        )
        if result is None:
            return
        intensity, duration_min, cont, auto_renew = result
        await self._client.write_rocking(intensity, duration_min, cont, auto_renew)

        # Optimistic refresh: firmware notifications are the source of truth,
        # but the panel should react immediately.
        if self._client.is_connected:
            if intensity == 0:
                sp.intensity_name = "OFF"
                sp.time_left_min = None
                sp.set_time_min = 0
            else:
                sp.intensity_name = INTENSITY_NAMES.get(intensity, "—")
                sp.time_left_min = duration_min
                sp.set_time_min = duration_min
            sp.cont_on_disc = cont
            sp.auto_renew_active = auto_renew and intensity != 0
            self._sync_header()

    def action_drive(self) -> None:
        self.run_worker(self._do_drive(), group="dialog")

    async def _do_drive(self) -> None:
        sp = self.query_one(StatusPanel)
        result = await self.push_screen_wait(
            DriveScreen(current_level=self._NAME_TO_DRIVE.get(sp.drive_mode, 1))
        )
        if result is None:
            return
        await self._client.write_drive(result)
        if self._client.is_connected:
            sp.drive_mode = DRIVE_NAMES.get(result, "—")
            self._sync_header()

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
