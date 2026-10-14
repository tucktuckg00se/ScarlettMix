"""Routing, output-level and input-switch tabs."""

from __future__ import annotations

from PySide6.QtCore import Qt, QSignalBlocker, Signal
from PySide6.QtWidgets import (QCheckBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMenu, QPushButton, QSizePolicy, QToolButton, QToolTip,
                               QVBoxLayout, QWidget)

from .. import names
from ..alsa import TYPE_ENUMERATED, ElemInfo
from ..device import Device, db_to_level, level_to_db
from ..monitor import BLUE, RED, SILVER, Monitor
from . import theme
from .widgets import (MUTE_COLOR, Fader, FitScrollArea, GroupedCombo, ItemCombo, MarkCheckBox,
                      ToggleButton, caption, card, fmt_db, section_title)


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


def _channel_entries(items: list[str]) -> list[tuple[str, str, str]]:
    """Single source channels for a selector, grouped like the pairs."""
    heading = {"PCM": "From the computer", "Mix": "Mixes", "Analog": "Inputs",
               "SPDIF": "Inputs", "ADAT": "Inputs"}
    return [("" if i == "Off" else heading.get(i.split(" ")[0], ""), names.channel(i), i)
            for i in items]


class PairRow(QWidget):
    """One stereo destination: what it is, one selector for a stereo source,
    and (behind the L/R button) a selector for each side."""

    def __init__(self, device: Device, name: str, socket: str, left: ElemInfo,
                 right: ElemInfo | None, tip: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.device, self.left, self.right = device, left, right
        self.elems = [e for e in (left, right) if e]
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(theme.S2)
        grid.setVerticalSpacing(theme.S1)

        # One line: what it is, then (muted) which sockets it shares.
        names_box = QHBoxLayout()
        names_box.setSpacing(theme.S2)
        self.name = QLabel(name)
        names_box.addWidget(self.name)
        if socket:
            names_box.addWidget(caption(socket))
        names_box.addStretch(1)
        self.names = QWidget()
        self.names.setLayout(names_box)
        names_box.setContentsMargins(0, 0, 0, 0)
        self.names.setToolTip(tip)
        grid.addWidget(self.names, 0, 0, Qt.AlignVCenter)

        self.pair = GroupedCombo()
        self.pair.setAccessibleName(f"{name} source")
        self.pair.setToolTip(tip)
        self.pair.set_entries([(h, t, f"{l}|{r}") for h, t, (l, r) in names.source_pairs(left.items)])
        self.pair.picked.connect(self._pair_picked)
        grid.addWidget(self.pair, 0, 1)

        self.split = QToolButton()
        self.split.setObjectName("split")
        self.split.setText("L/R")
        self.split.setCheckable(True)
        self.split.setToolTip("Choose the left and right source separately")
        grid.addWidget(self.split, 0, 2)

        self.sides = QWidget()
        sides = QHBoxLayout(self.sides)
        sides.setContentsMargins(0, 0, 0, 0)
        sides.setSpacing(theme.S1 + 2)
        self.side_combos = []
        for tag, e in zip("LR", (left, right)):
            if not e:
                continue
            sides.addWidget(caption(tag))
            combo = GroupedCombo()
            combo.setAccessibleName(f"{name} {'left' if tag == 'L' else 'right'} source")
            combo.set_entries(_channel_entries(e.items))
            combo.picked.connect(lambda item, e=e: self.device.set_item(e, item))
            sides.addWidget(combo, 1)
            self.side_combos.append((e, combo))
        grid.addWidget(self.sides, 1, 1, 1, 2)
        self.sides.hide()
        self.split.toggled.connect(self.sides.setVisible)
        self.sync()
        # A left/right choice that is not a stereo pair starts out shown in
        # full, so nothing is hidden behind the button.
        if not names.pair_name(*self._items(), self.left.items)[1]:
            self.split.setChecked(True)

    def _items(self) -> tuple[str, str]:
        items = [self.device.item(e) for e in self.elems]
        return items[0], items[-1]

    def sync(self) -> None:
        items = [self.device.item(e) for e in self.elems]
        left, right = items[0], items[-1]
        shown, is_pair = names.pair_name(left, right, self.left.items)
        self.pair.select(f"{left}|{right}", "" if is_pair else shown)
        for (e, combo), item in zip(self.side_combos, items):
            combo.select(item)

    def _pair_picked(self, key: str) -> None:
        left, right = key.split("|")
        self.device.set_item(self.left, left)
        if self.right:
            self.device.set_item(self.right, right)

    def mark(self, saved) -> bool:
        flags = [saved.control(e) for e in self.elems]
        self.pair.setUnsaved(any(flags))
        for (e, combo), flag in zip(self.side_combos, flags):
            combo.setUnsaved(flag)
        return any(flags)


def _pair_card(title: str, rows: list[PairRow], columns: int = 1) -> QFrame:
    """Rows of destinations in a titled panel; the names line up in a column."""
    frame = card()
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(theme.S3, theme.S2, theme.S3, theme.S3)
    lay.setSpacing(theme.S2)
    lay.addWidget(section_title(title))
    grid = QGridLayout()
    grid.setHorizontalSpacing(theme.S4)
    grid.setVerticalSpacing(theme.S2)
    per_column = (len(rows) + columns - 1) // columns
    width = max(r.names.sizeHint().width() for r in rows) if rows else 0
    for i, row in enumerate(rows):
        row.names.setFixedWidth(width)
        row.pair.setMinimumWidth(row.pair.fontMetrics().horizontalAdvance("Analog in 7/8") + 40)
        grid.addWidget(row, i % per_column, i // per_column)
    lay.addLayout(grid)
    lay.addStretch(1)
    return frame


class OutputRoutingPanel(QWidget):
    """What each physical output plays, one row per stereo pair, as on the
    back panel: analogue outputs (with the headphone sockets that share
    them) and digital outputs."""

    def __init__(self, device: Device, parent: QWidget | None = None):
        super().__init__(parent)
        self.device = device
        self._rows: dict[int, PairRow] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, theme.S2, 0, 0)
        # Shown in the corner of the tab bar by the main window.
        self.corner = preset = QToolButton()
        preset.setText("Routing preset")
        preset.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(preset)
        for title, hint, fn in (
            ("Clear", "Turn every output off", self.preset_clear),
            ("DAW Tracking", "DAW 1/2 to every analogue output pair", self.preset_daw_tracking),
            ("Zero Latency Tracking", "Mix 1/2 to every analogue output pair",
             self.preset_zero_latency),
            ("Mixing", "Each output plays the DAW channel with the same number", self.preset_mixing),
        ):
            act = menu.addAction(title, fn)
            act.setToolTip(hint)
        menu.setToolTipsVisible(True)
        preset.setMenu(menu)

        usb = device.backend.usb_id
        cards = QHBoxLayout()
        cards.setSpacing(theme.S3)
        groups = dict(self._groups())
        for heading, kinds in (("Analogue outputs", ("Monitor", "Line", "Headphones")),
                               ("Digital outputs", ("S/PDIF", "ADAT"))):
            rows = []
            for kind in kinds:
                for o in groups.get(kind, []):
                    if not o.source_l:
                        continue
                    name, socket = names.output_title(o.label, usb)
                    tip = (f"The {name.lower()} socket plays the same signal as {socket}"
                           if device.headphones(o) else "")
                    row = PairRow(device, name, socket if socket.startswith("Line") else "",
                                  o.source_l, o.source_r, tip)
                    rows.append(row)
                    for e in row.elems:
                        self._rows[e.numid] = row
            if rows:
                cards.addWidget(_pair_card(heading, rows))
        cards.addStretch(1)
        scroll = FitScrollArea()
        inner = QWidget()
        inner.setLayout(cards)
        cards.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)
        device.add_listener(self._changed)

    def mark(self, saved) -> bool:
        """Outline what is not saved to hardware; True if anything here is."""
        return any([r.mark(saved) for r in set(self._rows.values())])

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
        if row := self._rows.get(e.numid):
            row.sync()

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
    """What the computer records on each capture channel, one row per pair.
    The original keeps this fixed; the hardware lets it be chosen."""

    def __init__(self, device: Device, parent: QWidget | None = None):
        super().__init__(parent)
        self.device = device
        self._rows: dict[int, PairRow] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, theme.S2, 0, 0)
        # Shown in the corner of the tab bar by the main window.
        self.corner = reset = QToolButton()
        reset.setText("Reset to inputs")
        reset.setToolTip("Record each hardware input on the capture channel with the same position")
        reset.clicked.connect(self.reset)
        rows = []
        caps = device.capture
        for i in range(0, len(caps), 2):
            right = caps[i + 1] if i + 1 < len(caps) else None
            label = f"DAW in {i + 1}/{i + 2}" if right else f"DAW in {i + 1}"
            row = PairRow(device, label, "", caps[i], right,
                          "What the computer records on these capture channels")
            rows.append(row)
            for e in row.elems:
                self._rows[e.numid] = row
        cards = QHBoxLayout()
        cards.setContentsMargins(0, 0, 0, 0)
        cards.addWidget(_pair_card("Recorded by the computer", rows, columns=2))
        cards.addStretch(1)
        scroll = FitScrollArea()
        inner = QWidget()
        inner.setLayout(cards)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)
        device.add_listener(lambda e: self._rows[e.numid].sync() if e.numid in self._rows else None)

    def mark(self, saved) -> bool:
        return any([r.mark(saved) for r in set(self._rows.values())])

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
        self.setObjectName("strip")
        self.setMinimumWidth(96)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S2, theme.S2, theme.S2, theme.S2)
        lay.setSpacing(theme.S1 + 1)

        head = QLabel(title)
        head.setAlignment(Qt.AlignCenter)
        if bold:
            head.setObjectName("sectionTitle")      # a panel title, like the others
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
        root.setContentsMargins(0, theme.S2, 0, 0)
        scroll = FitScrollArea()
        inner = QWidget()
        row = QHBoxLayout(inner)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S1 + 2)

        def add(strip: LevelStrip) -> None:
            row.addWidget(strip)
            for e in (strip.volume, strip.switch):
                if e:
                    self._strips[e.numid] = strip

        for o in device.outputs:
            if o.volume or o.switch:
                name, socket = names.output_title(o.label, device.backend.usb_id)
                add(LevelStrip(device, name, o.volume, o.switch,
                               subtitle=socket if socket.startswith("Line") else ""))
        # One fixed width for every strip, whatever the window size: the row
        # scrolls when the window is narrow instead of squeezing the strips.
        strips = list(dict.fromkeys(self._strips.values()))
        width = max(s.sizeHint().width() for s in strips) if strips else 0
        for s in strips:
            s.setFixedWidth(width)
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

    COLORS = {BLUE: theme.MONITOR_BLUE, SILVER: theme.MONITOR_SILVER, RED: theme.MUTE}
    MEANING = {BLUE: "in the monitor group (knob, Mute and Dim)",
               SILVER: "independent of the monitor section",
               RED: "muted"}
    CYCLE = (BLUE, SILVER, RED)
    changed = Signal(int, str)

    def __init__(self, index: int, title: str, parent: QWidget | None = None):
        super().__init__(str(index + 1), parent)
        self.index, self.title, self.state = index, title, SILVER
        self.setFixedSize(34, 30)
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
            look = f"background-color: {color}; color: #111; border: 1px solid {color};"
        self.setStyleSheet(f"QPushButton {{ {look} font-weight: 600; padding: 0;"
                           f" border-radius: {theme.RADIUS}px; }}")
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

    LEGEND = ("<b>Output buttons</b><br>"
              "Blue: in the monitor group (the knob, Mute and Dim act on it)<br>"
              "Silver: independent, at full level<br>"
              "Red: muted<br>"
              "Outline: not saved to hardware<br><br>"
              "Click a button to move it to the next state.")

    changed = Signal()          # the user changed something here
    message = Signal(str)       # something worth telling the user

    def __init__(self, device: Device, monitor: Monitor, parent: QWidget | None = None):
        super().__init__(parent)
        self.device, self.monitor = device, monitor
        self._knob: int | None = None
        self._unsaved = [False] * len(monitor.channels)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.S2)

        head = QHBoxLayout()
        head.addWidget(section_title("Monitor"))
        head.addStretch(1)
        help_button = QToolButton()
        help_button.setText("?")
        help_button.setObjectName("help")
        help_button.setToolTip(self.LEGEND)
        help_button.clicked.connect(
            lambda: QToolTip.showText(help_button.mapToGlobal(help_button.rect().bottomLeft()),
                                      self.LEGEND, help_button))
        head.addWidget(help_button)
        lay.addLayout(head)

        self.knob = QLabel("\u2013")
        self.knob.setObjectName("knobValue")
        font = self.knob.font()
        font.setPointSizeF(font.pointSizeF() * 1.7)
        self.knob.setFont(font)
        self.knob.setAlignment(Qt.AlignCenter)
        self.knob.setToolTip("Position of the monitor knob on the front panel")
        lay.addWidget(self.knob)
        knob_caption = caption("monitor knob")
        knob_caption.setAlignment(Qt.AlignCenter)
        lay.addWidget(knob_caption)

        row = QHBoxLayout()
        self.dim = ToggleButton("Dim", theme.DIM, "Dim: the same as the front-panel button")
        self.mute = ToggleButton("Mute", MUTE_COLOR, "Mute: the same as the front-panel button")
        self.dim.clicked.connect(lambda on: device.write(device.hw_dim, [int(on)]))
        self.mute.clicked.connect(lambda on: device.write(device.hw_mute, [int(on)]))
        row.addWidget(self.dim)
        row.addWidget(self.mute)
        lay.addLayout(row)
        self.mono = self.pad = None
        extras = QHBoxLayout()
        if device.hw_mono:
            self.mono = ToggleButton("Mono", theme.LINK,
                                     "Mono: blue output pairs play left and right summed")
            self.mono.setChecked(monitor.mono)
            self.mono.clicked.connect(self._mono_clicked)
            extras.addWidget(self.mono)
        if device.hw_pad:
            self.pad = ToggleButton("Pad", theme.DIM,
                                    "Pad: makes the main monitor outputs quieter "
                                    "(labelled -20 dB in the original)")
            self.pad.setChecked(monitor.pad)
            self.pad.clicked.connect(self._pad_clicked)
            extras.addWidget(self.pad)
        if extras.count():
            lay.addLayout(extras)

        grid = QGridLayout()
        grid.setSpacing(theme.S1 + 1)
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
            preset.setText("Presets")
            preset.setPopupMode(QToolButton.InstantPopup)
            preset.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            preset.setToolButtonStyle(Qt.ToolButtonTextOnly)
            preset.setToolTip("Choose which outputs are in the monitor group; the rest "
                              "become independent")
            menu = QMenu(preset)
            for name, outs in monitor.presets():
                menu.addAction(f"{name}  ({', '.join(map(str, outs))})",
                               lambda outs=outs: self._use_preset(outs))
            preset.setMenu(menu)
            lay.addWidget(preset)
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
        self.knob.setText("\u2212\u221e" if knob >= 127 else f"\u2212{knob} dB" if knob else "0 dB")
        self.mute.setChecked(mute)
        self.dim.setChecked(dim)
