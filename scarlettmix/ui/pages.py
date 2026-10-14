"""Routing, output-level and input-switch tabs."""

from __future__ import annotations

from PySide6.QtCore import Qt, QSignalBlocker, Signal
from PySide6.QtWidgets import (QCheckBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget)

from .. import names
from ..alsa import TYPE_ENUMERATED, ElemInfo
from ..device import Device, db_to_level, level_to_db
from ..monitor import BLUE, RED, SILVER, Monitor
from .widgets import (MUTE_COLOR, Fader, FitScrollArea, ItemCombo, MarkCheckBox, ToggleButton,
                      fmt_db)


class EnumBinding:
    """Keeps a combo box and an enumerated control in step."""

    def __init__(self, device: Device, elem: ElemInfo, accessible: str):
        self.device, self.elem = device, elem
        self.combo = ItemCombo(elem.items)
        self.combo.setAccessibleName(accessible)
        self.combo.setEnabled(elem.writable)
        self.combo.currentIndexChanged.connect(lambda i: device.set(elem, [i]))
        self.sync()

    def sync(self) -> None:
        with QSignalBlocker(self.combo):
            self.combo.setCurrentIndex(self.device.get(self.elem)[0])

    def mark(self, saved) -> bool:
        on = saved.control(self.elem)
        self.combo.setUnsaved(on)
        return on


class OutputRoutingPanel(QWidget):
    """Source selector for every physical output, in the original's grid:
    monitor outputs first, then line, ADAT and S/PDIF outputs four to a row."""

    COLUMNS = 4

    def __init__(self, device: Device, parent: QWidget | None = None):
        super().__init__(parent)
        self.device = device
        self._bindings: dict[int, EnumBinding] = {}

        root = QVBoxLayout(self)
        preset = QToolButton()
        preset.setText("Routing Preset\u2026")
        preset.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(preset)
        for title, hint, fn in (
            ("Clear", "Turn every output off", self.preset_clear),
            ("DAW Tracking", "DAW 1 and 2 to every analogue output pair", self.preset_daw_tracking),
            ("Zero Latency Tracking", "Mix 1 and 2 to every analogue output pair",
             self.preset_zero_latency),
            ("Mixing", "Each output plays the DAW channel with the same number", self.preset_mixing),
        ):
            act = menu.addAction(title, fn)
            act.setToolTip(hint)
        menu.setToolTipsVisible(True)
        preset.setMenu(menu)

        scroll = FitScrollArea()
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(5)
        last_col = self.COLUMNS * 2
        row = 0
        for title, outputs in self._groups():
            if row:
                line = QFrame()
                line.setFrameShape(QFrame.HLine)
                line.setFrameShadow(QFrame.Sunken)
                grid.addWidget(line, row, 0, 1, last_col + 1)
                row += 1
            head = QLabel(title)
            font = head.font()
            font.setBold(True)
            head.setFont(font)
            grid.addWidget(head, row, 0, Qt.AlignLeft | Qt.AlignVCenter)

            cells = []
            for o in outputs:
                hp = device.headphones(o)
                plain = names.output_channels(o.label)
                for side, label, e in zip("LR", plain, (o.source_l, o.source_r)):
                    if not e:
                        continue
                    if hp:
                        # Say what it is first, and which line output it shares second.
                        cells.append((f"Headphones {hp} {side}", label, e))
                    else:
                        cells.append((label, "", e))
            for i, (label, also, e) in enumerate(cells):
                r, col = row + i // self.COLUMNS, 1 + (i % self.COLUMNS) * 2
                bind = EnumBinding(device, e, f"{label} source")
                bind.combo.setSizeAdjustPolicy(ItemCombo.AdjustToMinimumContentsLengthWithIcon)
                bind.combo.setMinimumContentsLength(8)
                self._bindings[e.numid] = bind
                grid.addWidget(bind.combo, r, col)
                text = QLabel(f"\u2192 {label}"
                              + (f"<br><small>&nbsp;&nbsp;&nbsp;&nbsp;{also}</small>" if also else ""))
                if also:
                    text.setToolTip(f"The headphone socket plays the same signal as {also}")
                grid.addWidget(text, r, col + 1)
            if not row:
                # As in the original, the preset menu shares the monitor outputs' row.
                grid.addWidget(preset, 0, last_col - 1, 1, 2, Qt.AlignRight)
            row += max(1, (len(cells) + self.COLUMNS - 1) // self.COLUMNS)
        grid.setRowStretch(row, 1)
        for i in range(self.COLUMNS):
            grid.setColumnStretch(1 + i * 2, 1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)
        device.add_listener(self._changed)

    def mark(self, saved) -> bool:
        """Outline what is not saved to hardware; True if anything here is."""
        return any([b.mark(saved) for b in self._bindings.values()])

    def _groups(self) -> list[tuple[str, list]]:
        """Output pairs under the headings shown, in the original's order with
        the pairs that feed a headphone socket pulled out under their own name."""
        def kind(o):
            if "ADAT" in o.label:
                return "ADAT"
            if "SPDIF" in o.label:
                return "S/PDIF"
            if self.device.headphones(o):
                return "Headphones"
            return "Line" if o.label.startswith("Line") else "Monitor"
        return [(k, [o for o in self.device.outputs if kind(o) == k])
                for k in ("Monitor", "Line", "Headphones", "ADAT", "S/PDIF")
                if any(kind(o) == k for o in self.device.outputs)]

    def _analogue(self) -> list:
        return [o for k, outs in self._groups()
                if k in ("Monitor", "Line", "Headphones") for o in outs]

    def _changed(self, e: ElemInfo) -> None:
        if b := self._bindings.get(e.numid):
            b.sync()

    def _set_pairs(self, outputs: list, left: str, right: str) -> None:
        for o in outputs:
            if o.source_l:
                self.device.set_item(o.source_l, left)
            if o.source_r:
                self.device.set_item(o.source_r, right)

    def preset_clear(self) -> None:
        self._set_pairs(self.device.outputs, "Off", "Off")

    def preset_daw_tracking(self) -> None:
        self._set_pairs(self._analogue(), "PCM 1", "PCM 2")

    def preset_zero_latency(self) -> None:
        self._set_pairs(self._analogue(), "Mix A", "Mix B")

    def preset_mixing(self) -> None:
        for i, (_, e) in enumerate(self.device.output_sources()):
            self.device.set_item(e, f"PCM {i + 1}")


class CaptureRoutingPanel(QWidget):
    """What the computer records on each capture channel. The original keeps
    this fixed; the hardware lets it be chosen, so it is offered here."""

    COLUMNS = 3

    def __init__(self, device: Device, parent: QWidget | None = None):
        super().__init__(parent)
        self.device = device
        self._bindings: dict[int, EnumBinding] = {}
        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        reset = QPushButton("Reset to hardware inputs")
        reset.setToolTip("Record each hardware input on the capture channel with the same position")
        reset.clicked.connect(self.reset)
        bar.addWidget(reset)
        bar.addStretch(1)
        root.addLayout(bar)
        scroll = FitScrollArea()
        inner = QWidget()
        grid = QGridLayout(inner)
        rows = (len(device.capture) + self.COLUMNS - 1) // self.COLUMNS
        for i, e in enumerate(device.capture):
            bind = EnumBinding(device, e, f"DAW input {i + 1} source")
            self._bindings[e.numid] = bind
            r, c = i % rows, (i // rows) * 2
            grid.addWidget(bind.combo, r, c)
            grid.addWidget(QLabel(f"\u2192 DAW Input {i + 1}"), r, c + 1)
        grid.setRowStretch(rows, 1)
        for c in range(self.COLUMNS):
            grid.setColumnStretch(c * 2, 1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)
        device.add_listener(lambda e: self._bindings[e.numid].sync()
                            if e.numid in self._bindings else None)

    def mark(self, saved) -> bool:
        return any([b.mark(saved) for b in self._bindings.values()])

    def reset(self) -> None:
        for i, e in enumerate(self.device.capture):
            hardware = [it for it in e.items
                        if it != "Off" and not it.startswith(("PCM ", "Mix "))]
            if i < len(hardware):
                self.device.set_item(e, hardware[i])


class LevelStrip(QFrame):
    """Fader plus mute for a volume control with any number of channels."""

    def __init__(self, device: Device, title: str, volume: ElemInfo | None,
                 switch: ElemInfo | None, bold: bool = False, listen: bool = False,
                 subtitle: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.device, self.volume, self.switch = device, volume, switch
        self._on_knob = False
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumWidth(96)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(5)

        head = QLabel(title)
        head.setAlignment(Qt.AlignCenter)
        if bold:
            font = head.font()
            font.setBold(True)
            head.setFont(font)
        lay.addWidget(head)
        if subtitle is not None:
            # Always present in a row of strips so the faders line up.
            sub = QLabel(f"<small>{subtitle or '&nbsp;'}</small>")
            sub.setAlignment(Qt.AlignCenter)
            lay.addWidget(sub)

        self.fader = None
        self.value = QLabel()
        self.value.setAlignment(Qt.AlignCenter)
        if volume:
            self.fader = Fader(level_to_db(volume.min), level_to_db(volume.max))
            self.fader.setAccessibleName(f"{title} level")
            self.fader.setEnabled(volume.writable)
            self.fader.changed.connect(self._fader_changed)
            lay.addWidget(self.fader, 1, Qt.AlignHCenter)
        else:
            lay.addStretch(1)
        lay.addWidget(self.value)

        self.mute = None
        if switch:
            self.mute = ToggleButton("Mute", MUTE_COLOR, f"Mute {title}")
            self.mute.setEnabled(switch.writable)
            self.mute.toggled.connect(self._mute_changed)
            lay.addWidget(self.mute)
        self.sync()
        if listen:  # standalone use; OutputsPage dispatches to its own strips
            ids = {e.numid for e in (volume, switch) if e}
            device.add_listener(lambda e: self.sync() if e.numid in ids else None)

    def sync(self) -> None:
        if self.fader:
            db = level_to_db(max(self.device.get(self.volume)))
            self.fader.setDb(db)
            self.value.setText("monitor knob" if self._on_knob else fmt_db(db) + " dB")
        if self.mute:
            # The driver's switch is "on = audible".
            with QSignalBlocker(self.mute):
                self.mute.setChecked(not any(self.device.get(self.switch)))

    def set_on_knob(self, on: bool) -> None:
        """Grey the fader out while the monitor knob is in charge of this level."""
        if self.fader and on != self._on_knob:
            self._on_knob = on
            self.fader.setEnabled(self.volume.writable and not on)
            self.fader.setToolTip("The monitor knob controls this output." if on else "")
            self.sync()

    def mark(self, saved) -> bool:
        level, mute = saved.control(self.volume), saved.control(self.switch)
        if self.fader:
            self.fader.setUnsaved(level)
        if self.mute:
            self.mute.setUnsaved(mute)
        return level or mute

    def _fader_changed(self, db: float) -> None:
        self.value.setText(fmt_db(db) + " dB")
        self.device.set(self.volume, [db_to_level(db, self.volume)] * self.volume.count)

    def _mute_changed(self, muted: bool) -> None:
        self.device.set(self.switch, [0 if muted else 1] * self.switch.count)


class OutputsPage(QWidget):
    def __init__(self, device: Device, parent: QWidget | None = None):
        super().__init__(parent)
        self._strips: dict[int, LevelStrip] = {}
        root = QVBoxLayout(self)
        scroll = FitScrollArea()
        inner = QWidget()
        row = QHBoxLayout(inner)
        row.setSpacing(4)

        def add(strip: LevelStrip) -> None:
            row.addWidget(strip)
            for e in (strip.volume, strip.switch):
                if e:
                    self._strips[e.numid] = strip

        for o in device.outputs:
            if o.volume or o.switch:
                hp = device.headphones(o)
                add(LevelStrip(device, f"Headphones {hp}" if hp else o.label, o.volume, o.switch,
                               subtitle=o.label if hp else ""))
        row.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll)
        device.add_listener(self._changed)

    def mark(self, saved) -> bool:
        return any([s.mark(saved) for s in set(self._strips.values())])

    def set_on_knob(self, names: set[str]) -> None:
        for s in set(self._strips.values()):
            s.set_on_knob(bool(s.volume) and s.volume.name in names)

    def _changed(self, e: ElemInfo) -> None:
        if s := self._strips.get(e.numid):
            s.sync()


class InputsPage(QWidget):
    """Per-input hardware switches (impedance, pad, gain) on models that have them."""

    def __init__(self, device: Device, parent: QWidget | None = None):
        super().__init__(parent)
        self.device = device
        self._sync = {}
        self._marks = []            # (control, widget)
        form = QFormLayout(self)
        for e in device.input_switches:
            label = e.name.removesuffix(" Switch")
            if e.type == TYPE_ENUMERATED:
                b = EnumBinding(device, e, label)
                self._sync[e.numid] = b.sync
                self._marks.append((e, b.combo))
                form.addRow(label, b.combo)
            else:
                box = MarkCheckBox()
                self._marks.append((e, box))
                box.setAccessibleName(label)
                box.setEnabled(e.writable)
                box.toggled.connect(lambda on, e=e: device.set(e, [int(on)] * e.count))

                def sync(box=box, e=e):
                    with QSignalBlocker(box):
                        box.setChecked(bool(device.get(e)[0]))

                sync()
                self._sync[e.numid] = sync
                form.addRow(label, box)
        device.add_listener(lambda e: self._sync.get(e.numid, lambda: None)())

    def mark(self, saved) -> bool:
        found = False
        for e, widget in self._marks:
            on = saved.control(e)
            widget.setUnsaved(on)
            found = found or on
        return found



class OutputStateButton(QPushButton):
    """One of the monitor section's output buttons: blue, silver or red."""

    COLORS = {BLUE: "#4a90d9", SILVER: "#b8bcc2", RED: "#d9534f"}
    MEANING = {BLUE: "in the monitor group (knob, Mute and Dim)",
               SILVER: "independent of the monitor section",
               RED: "muted"}
    CYCLE = (BLUE, SILVER, RED)
    changed = Signal(int, str)

    def __init__(self, index: int, title: str, parent: QWidget | None = None):
        super().__init__(str(index + 1), parent)
        self.index, self.title, self.state = index, title, SILVER
        self.setFixedSize(34, 28)
        self.clicked.connect(self._clicked)
        self.set_state(SILVER)

    def set_state(self, state: str, unsaved: bool = False) -> None:
        """``unsaved``: drawn as an outline, because this output's state is
        not what the interface has stored."""
        self.state = state
        color = self.COLORS[state]
        if unsaved:
            ink = "#666" if state == SILVER else color
            look = f"background-color: transparent; color: {ink}; border: 2px solid {color};"
        else:
            look = f"background-color: {color}; color: #111; border: 1px solid #555;"
        self.setStyleSheet(f"QPushButton {{ {look} font-weight: bold; border-radius: 4px; }}")
        self.setToolTip(f"{self.title}: {self.MEANING[state]}."
                        + ("\nNot saved to hardware." if unsaved else "")
                        + "\nClick to change: monitor group, independent, muted.")
        self.setAccessibleName(f"{self.title} monitor state: {state}"
                               + (", not saved" if unsaved else ""))

    def _clicked(self) -> None:
        # Each click moves to the next state. (The original hides silver behind shift-click.)
        new = self.CYCLE[(self.CYCLE.index(self.state) + 1) % len(self.CYCLE)]
        self.set_state(new)
        self.changed.emit(self.index, new)


class MonitorPanel(QWidget):
    """Front-panel readout, Dim and Mute, and the ten output buttons."""

    changed = Signal()          # the user changed something here
    message = Signal(str)       # something worth telling the user

    def __init__(self, device: Device, monitor: Monitor, parent: QWidget | None = None):
        super().__init__(parent)
        self.device, self.monitor = device, monitor
        self._knob: int | None = None
        self._unsaved = [False] * len(monitor.channels)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.knob = QLabel("Knob")
        self.knob.setAlignment(Qt.AlignCenter)
        self.knob.setToolTip("Position of the monitor knob on the front panel")
        lay.addWidget(self.knob)

        row = QHBoxLayout()
        self.dim = ToggleButton("Dim", "#e0a800", "Dim: the same as the front-panel button")
        self.mute = ToggleButton("Mute", MUTE_COLOR, "Mute: the same as the front-panel button")
        self.dim.clicked.connect(lambda on: device.write(device.hw_dim, [int(on)]))
        self.mute.clicked.connect(lambda on: device.write(device.hw_mute, [int(on)]))
        row.addWidget(self.dim)
        row.addWidget(self.mute)
        lay.addLayout(row)
        self.mono = self.pad = None
        extras = QHBoxLayout()
        if device.hw_mono:
            self.mono = ToggleButton("Mono", "#5b9bd5",
                                     "Mono: blue output pairs play left and right summed")
            self.mono.setChecked(monitor.mono)
            self.mono.clicked.connect(self._mono_clicked)
            extras.addWidget(self.mono)
        if device.hw_pad:
            self.pad = ToggleButton("Pad", "#e0a800",
                                    "Pad: makes the main monitor outputs quieter "
                                    "(labelled -20 dB in the original)")
            self.pad.setChecked(monitor.pad)
            self.pad.clicked.connect(self._pad_clicked)
            extras.addWidget(self.pad)
        if extras.count():
            lay.addLayout(extras)

        grid = QGridLayout()
        grid.setSpacing(4)
        self.buttons = []
        for i, (output, side) in enumerate(monitor.channels):
            name = names.output_channels(output.label)[side]
            hp = device.headphones(output)
            b = OutputStateButton(i, f"{name} (Headphones {hp})" if hp else name)
            b.changed.connect(self._state_changed)
            grid.addWidget(b, i % 2, i // 2)       # odd outputs on top, as in the original
            self.buttons.append(b)
        lay.addLayout(grid)
        if monitor.presets():
            preset = QToolButton()
            preset.setText("Monitor Preset\u2026")
            preset.setPopupMode(QToolButton.InstantPopup)
            preset.setToolButtonStyle(Qt.ToolButtonTextOnly)
            preset.setToolTip("Choose which outputs are in the monitor group; the rest "
                              "become independent")
            menu = QMenu(preset)
            for name, outs in monitor.presets():
                menu.addAction(f"{name}  ({', '.join(map(str, outs))})",
                               lambda outs=outs: self._use_preset(outs))
            preset.setMenu(menu)
            lay.addWidget(preset, 0, Qt.AlignHCenter)
        hint = QLabel("<small>blue: monitor group (knob, Mute, Dim)<br>silver: independent"
                      "<br>red: muted<br>outline: not saved to hardware</small>")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)
        self.sync()

    def sync(self) -> None:
        """Draw the buttons as the monitor object last read them."""
        for b, state, unsaved in zip(self.buttons, self.monitor.states, self._unsaved):
            b.set_state(state, unsaved)
        if self.mono:
            self.mono.setChecked(self.monitor.mono)
        if self.pad:
            self.pad.setChecked(self.monitor.pad)

    def mark(self, saved) -> bool:
        """Outline what is not saved to hardware; True if anything here is."""
        self._unsaved = saved.monitor(self.monitor)
        mono, pad = saved.mono(self.monitor), saved.pad(self.monitor)
        if self.mono:
            self.mono.setUnsaved(mono)
        if self.pad:
            self.pad.setUnsaved(pad)
        self.sync()
        return any(self._unsaved) or mono or pad

    def refresh(self) -> None:
        """Re-read the monitor section from the interface (reads only)."""
        if self.monitor.refresh():
            self.sync()

    def _mono_clicked(self, on: bool) -> None:
        self.monitor.set_mono(on)
        self.sync()
        self.changed.emit()

    def _pad_clicked(self, on: bool) -> None:
        self.monitor.set_pad(on)
        self.sync()
        self.changed.emit()

    def _use_preset(self, outputs: list[int]) -> None:
        self.monitor.use_preset(outputs)
        if self._knob is not None:
            self.monitor.follow(self._knob)
        self.sync()
        self.changed.emit()

    def _state_changed(self, index: int, state: str) -> None:
        output, side = self.monitor.channels[index]
        own = self.device.get(output.volume)[side] if output.volume else None
        self.monitor.set_state(index, state)
        if state == BLUE and own is not None and level_to_db(own) < 0:
            # The knob replaces an output's level; it does not turn down from it.
            what = (f"Headphones {self.device.headphones(output)}"
                    if self.device.headphones(output) else f"Output {index + 1}")
            self.message.emit(f"{what} is now on the monitor knob: its own level "
                              f"({level_to_db(own):+d} dB) no longer limits how loud it gets.")
        if self._knob is not None:
            self.monitor.follow(self._knob)
        self.sync()
        self.changed.emit()

    def set_status(self, knob: int, mute: bool, dim: bool) -> None:
        """Show the front panel, and do the knob's job for blue outputs the
        interface does not move by itself yet."""
        self._knob = knob
        self.monitor.follow(knob)
        self.knob.setText("Knob  " + ("-\u221e" if knob >= 127 else f"{-knob:+d} dB" if knob else "0 dB"))
        self.mute.setChecked(mute)
        self.dim.setChecked(dim)
