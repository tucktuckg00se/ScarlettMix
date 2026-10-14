"""The mixer tab: one channel strip per mixer input, for the selected mix."""

from __future__ import annotations

from PySide6.QtCore import Qt, QSignalBlocker, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QSizePolicy, QSlider, QToolButton, QVBoxLayout,
                               QWidget)

from .. import names
from ..device import Device
from ..mixer import Mix, MixController
from . import theme
from .widgets import (MUTE_COLOR, SOLO_COLOR, Fader, FitScrollArea, ItemCombo, MarkCheckBox,
                      MarkSlider, ToggleButton, caption, fmt_db)

STRIP_WIDTH = 86


def source_font(widget: QWidget):
    """Type for the strips' source selectors (the window's own)."""
    return widget.font()


def strip_width(widget: QWidget) -> int:
    """Strip width that fits the longest source name in full: the selector's
    padding and border (14) plus the strip's margins (10)."""
    names_ = ("Analog 18", "SPDIF 2", "ADAT 8", "DAW 18", "Mix H")
    widest = max(QFontMetrics(source_font(widget)).horizontalAdvance(n) for n in names_)
    return max(STRIP_WIDTH, widest + 30)


def fmt_pan(pan: float) -> str:
    v = round((pan - 0.5) * 200)
    return "C" if v == 0 else (f"L{-v}" if v < 0 else f"R{v}")


LINK_COLOR = theme.LINK


class ChannelStrip(QFrame):
    """One mixer channel: a single input, or two inputs joined as a stereo channel."""

    edited = Signal()
    link_toggled = Signal(int, bool)    # input pair index, joined?

    def __init__(self, indices: list[int], device: Device, parent: QWidget | None = None):
        super().__init__(parent)
        self.indices = indices
        self.index = indices[0]
        self.device = device
        self.linked = len(indices) == 2
        self.routes = [device.matrix_inputs[i] for i in indices]
        self.setObjectName("strip")
        self.setFixedWidth(strip_width(self) * len(indices))
        label = "/".join(str(i + 1) for i in indices)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S1 + 1, theme.S2 - 2, theme.S1 + 1, theme.S2 - 2)
        lay.setSpacing(theme.S1 + 1)

        # Header: the input number, and on the left input of each pair the
        # switch that joins the pair into one stereo channel (as in the original).
        head = QHBoxLayout()
        head.setSpacing(theme.S1)
        self.number = caption(label)
        self.number.setToolTip(("Mixer inputs " if self.linked else "Mixer input ") + label)
        head.addWidget(self.number, 1)
        self.link = None
        partner = self.index + 1
        if self.index % 2 == 0 and partner < device.n_inputs:
            self.link = ToggleButton(
                "Link", LINK_COLOR,
                f"Join inputs {self.index + 1} and {partner + 1} as one stereo channel in every mix")
            self.link.setObjectName("linkToggle")
            self.link.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.link.setFixedSize(42, 20)
            self.link.setUnsaved(False, force=True)     # restyle for its compact size
            font = self.link.font()
            font.setPointSizeF(font.pointSizeF() * 0.85)
            self.link.setFont(font)
            self.link.setChecked(self.linked)
            self.link.toggled.connect(lambda on: self.link_toggled.emit(self.index // 2, on))
            head.addWidget(self.link)
        else:
            spacer = QWidget()
            spacer.setFixedSize(0, 20)
            head.addWidget(spacer)
        lay.addLayout(head)

        sources = QHBoxLayout()
        sources.setSpacing(4)
        self.sources = []
        for i, route in zip(indices, self.routes):
            combo = ItemCombo(route.items)
            combo.setFont(source_font(self))
            combo.setObjectName("sourceSelect")     # no arrow: the whole name is the button
            combo.setAccessibleName(f"Mixer input {i + 1} source")
            combo.setSizeAdjustPolicy(ItemCombo.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(6)
            combo.currentIndexChanged.connect(lambda v, route=route: device.set(route, [v]))
            sources.addWidget(combo)
            self.sources.append(combo)
        lay.addLayout(sources)

        self.pan = MarkSlider(Qt.Horizontal)
        self.pan.setRange(-50, 50)
        self.pan.setPageStep(10)
        self.pan.setAccessibleName(f"Input {label} " + ("balance" if self.linked else "pan"))
        self.pan.valueChanged.connect(self._pan_changed)
        self.pan_label = QLabel("C")
        self.pan_label.setAlignment(Qt.AlignCenter)
        self.pan_label.setToolTip(("Balance" if self.linked else "Pan") + " (double-click to centre)")
        self.pan_label.mouseDoubleClickEvent = lambda ev: self.pan.setValue(0)
        # Hidden for mono mixes, but keeping its space so faders line up.
        for w in (self.pan, self.pan_label):
            policy = w.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            w.setSizePolicy(policy)
        pan_row = QHBoxLayout()
        pan_row.setSpacing(theme.S1)
        pan_row.addWidget(self.pan, 1)
        self.pan_label.setFixedWidth(self.fontMetrics().horizontalAdvance("R100") + 2)
        pan_row.addWidget(self.pan_label)
        lay.addLayout(pan_row)

        self.fader = Fader(meters=len(indices) if device.has_meters else 0)
        self.fader.setAccessibleName(f"Input {label} level")
        self.fader.setToolTip("Level in dB. Double-click for 0 dB; wheel or arrow keys for 1 dB steps.")
        self.fader.changed.connect(self._fader_changed)
        lay.addWidget(self.fader, 1, Qt.AlignHCenter)

        self.value = QLabel()
        self.value.setObjectName("value")
        self.value.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.value)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        self.mute = ToggleButton("M", MUTE_COLOR, f"Mute input {label}")
        self.solo = ToggleButton("S", SOLO_COLOR, f"Solo input {label}")
        self.mute.toggled.connect(self._button_changed)
        self.solo.toggled.connect(self._button_changed)
        buttons.addWidget(self.mute)
        buttons.addWidget(self.solo)
        lay.addLayout(buttons)

        self._strip = None
        for i in indices:
            self.sync_source(i)

    def bind(self, mix: Mix) -> None:
        """Show this channel's settings in the given mix."""
        self._strip = None
        strip = mix.strips[self.index]   # a stereo channel is driven by its left input's strip
        self.pan.setVisible(mix.stereo)
        self.pan_label.setVisible(mix.stereo)
        self.pan.setValue(round((strip.pan - 0.5) * 100))
        self.pan_label.setText(fmt_pan(strip.pan))
        self.fader.setDb(strip.db)
        self.value.setText(fmt_db(self.fader.db()) + " dB")
        self.mute.setChecked(strip.mute)
        self.solo.setChecked(strip.solo)
        self._strip = strip

    def mark(self, saved, model, mix: Mix) -> None:
        """Outline the controls whose setting is not saved to hardware."""
        for combo, route in zip(self.sources, self.routes):
            combo.setUnsaved(saved.control(route))
        changed = saved.strip(mix, self.index, self.indices)
        self.fader.setUnsaved(changed["fader"])
        self.pan.setUnsaved(changed["pan"])
        self.mute.setUnsaved(changed["mute"])
        self.solo.setUnsaved(changed["solo"])
        if self.link:
            self.link.setUnsaved(saved.link(model, self.index // 2))

    def set_meters(self, meters: dict[str, list[float]]) -> None:
        """Show the level of whatever source feeds each of this strip's inputs."""
        levels = []
        for route in self.routes:
            where = self.device.source_meter(self.device.item(route))
            levels.append(meters[where[0]][where[1]] if where else Fader.METER_FLOOR)
        self.fader.setLevels(levels)

    def sync_source(self, input_index: int) -> None:
        combo = self.sources[self.indices.index(input_index)]
        route = self.routes[self.indices.index(input_index)]
        with QSignalBlocker(combo):
            combo.setCurrentIndex(self.device.get(route)[0])
        combo.setToolTip(f"Mixer input {input_index + 1}: {combo.currentText()} (shared by all mixes)")

    def _fader_changed(self, db: float) -> None:
        self.value.setText(fmt_db(db) + " dB")
        if self._strip:
            self._strip.db = db
            self.edited.emit()

    def _pan_changed(self, v: int) -> None:
        pan = 0.5 + v / 100.0
        self.pan_label.setText(fmt_pan(pan))
        if self._strip:
            self._strip.pan = pan
            self.edited.emit()

    def _button_changed(self) -> None:
        if self._strip:
            self._strip.mute = self.mute.isChecked()
            self._strip.solo = self.solo.isChecked()
            self.edited.emit()


class MasterStrip(QFrame):
    edited = Signal()
    copy_requested = Signal(int)        # first bus of the mix to copy onto

    def __init__(self, device: Device, parent: QWidget | None = None):
        super().__init__(parent)
        self.device = device
        self.setObjectName("strip")
        self.setFixedWidth(strip_width(self) + 30)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S2, theme.S2, theme.S2, theme.S2)
        lay.setSpacing(theme.S1 + 1)

        head = QHBoxLayout()
        head.setSpacing(theme.S1)
        self.title = QLabel()
        self.title.setObjectName("sectionTitle")
        head.addWidget(self.title, 1)
        self.copy = QToolButton()
        self.copy.setObjectName("more")
        self.copy.setText("\u22ef")
        self.copy.setFixedHeight(20)
        self.copy.setPopupMode(QToolButton.InstantPopup)
        self.copy.setToolTip("Copy this mix's levels, pans, mutes and master to another mix")
        self.copy_menu = QMenu(self.copy)
        self.copy.setMenu(self.copy_menu)
        head.addWidget(self.copy)
        lay.addLayout(head)

        # The original's mix-output selector: where this mix is heard.
        self.send = QToolButton()
        self.send.setPopupMode(QToolButton.InstantPopup)
        self.send.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.send.setSizePolicy(self.send.sizePolicy().horizontalPolicy().Expanding,
                                self.send.sizePolicy().verticalPolicy())
        self.send.setAccessibleName("Send this mix to outputs")
        self.send_menu = QMenu(self.send)
        self.send.setMenu(self.send_menu)
        self._send_actions = []
        for o in device.outputs:
            if not o.sources:
                continue
            act = self.send_menu.addAction(names.output_pair(o.label, device.backend.usb_id))
            act.setCheckable(True)
            act.toggled.connect(lambda on, o=o: self._send_toggled(o, on))
            self._send_actions.append((o, act))
        lay.addWidget(self.send)
        # Same height as a channel strip's pan row, so the faders line up.
        self._pan_space = QWidget()
        self._pan_space.setFixedHeight(QSlider(Qt.Horizontal).sizeHint().height())
        lay.addWidget(self._pan_space)

        self.fader = Fader(meters=2 if device.has_meters else 0)
        self.fader.setAccessibleName("Mix master level")
        self.fader.changed.connect(self._changed)
        lay.addWidget(self.fader, 1, Qt.AlignHCenter)

        self.value = QLabel()
        self.value.setObjectName("value")
        self.value.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.value)

        self.mute = ToggleButton("Mute", MUTE_COLOR, "Mute this mix")
        self.mute.toggled.connect(self._changed)
        lay.addWidget(self.mute)
        self._mix = None

    def bind(self, mix: Mix, name: str, others: list[tuple[str, int]] = ()) -> None:
        self._mix = None
        self.title.setText(name)
        self.copy_menu.clear()
        self.copy_menu.addSection("Copy to")
        for other_name, bus in others:
            self.copy_menu.addAction(other_name, lambda bus=bus: self.copy_requested.emit(bus))
        self.copy.setEnabled(bool(others))
        self._buses = list(mix.buses)
        self.sync_send()
        self.fader.setDb(mix.master_db)
        self.value.setText(fmt_db(self.fader.db()) + " dB")
        self.mute.setChecked(mix.master_mute)
        self._mix = mix

    def mark(self, saved, mix: Mix) -> None:
        changed = saved.master(mix)
        self.fader.setUnsaved(changed["fader"])
        self.mute.setUnsaved(changed["mute"])

    def sync_send(self) -> None:
        """Tick the outputs this mix is playing on and summarise them on the button."""
        on, short = [], []
        for o, act in self._send_actions:
            sent = self.device.mix_sent_to(self._buses, o)
            with QSignalBlocker(act):
                act.setChecked(sent)
            if sent:
                hp = self.device.headphones(o)
                on.append(names.output_title(o.label, self.device.backend.usb_id)[0])
                short.append(f"Phones {hp}" if hp else o.label.replace("Monitor", "Main"))
        brief = ", ".join(short)
        if len(brief) > 14:
            brief = f"{len(short)} outputs"
        self.send.setText("\u2192 " + brief if short else "Send to")
        self.send.setToolTip("Outputs playing this mix: " + (", ".join(on) if on else "none")
                             + ".\nA mono mix is sent to both sides of a pair.")

    def _send_toggled(self, o, on: bool) -> None:
        self.device.send_mix(self._buses, o, on)
        self.sync_send()

    def _changed(self, *_):
        self.value.setText(fmt_db(self.fader.db()) + " dB")
        if self._mix:
            self._mix.master_db = self.fader.db()
            self._mix.master_mute = self.mute.isChecked()
            self.edited.emit()


class MixerPage(QWidget):
    model_changed = Signal()
    message = Signal(str)

    def __init__(self, device: Device, controller: MixController, parent: QWidget | None = None):
        super().__init__(parent)
        self.device = device
        self.controller = controller
        self._applying = False
        self._current_bus = 0
        self._saved = None
        self._gain_ids = {e.numid for e in device.matrix_gain.values()}
        self._route_ids = {e.numid: i for i, e in enumerate(device.matrix_inputs)}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.S2)

        top = QHBoxLayout()
        self._selector_box = QHBoxLayout()
        self._selector_box.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        top.addLayout(self._selector_box)
        top.addSpacing(16)
        self.stereo = MarkCheckBox("Stereo pair")
        self.stereo.setToolTip("Link this mix with its neighbour as a left/right pair with pan controls")
        self.stereo.toggled.connect(self._stereo_toggled)
        top.addWidget(self.stereo)
        top.addStretch(1)
        self.adopted = QLabel()
        top.addWidget(self.adopted)
        root.addLayout(top)

        scroll = FitScrollArea()
        inner = QWidget()
        row = QHBoxLayout(inner)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S1)
        self._row = row
        self.strips: list[ChannelStrip] = []
        row.addStretch(1)
        scroll.setWidget(inner)

        body = QHBoxLayout()
        body.setSpacing(theme.S2)
        body.addWidget(scroll, 1)
        self.master = MasterStrip(device)
        self.master.edited.connect(self._edited)
        self.master.copy_requested.connect(self._copy_to)
        body.addWidget(self.master)
        root.addLayout(body, 1)

        device.add_listener(self._device_changed)
        self.rebuild()

    # -- structure -----------------------------------------------------------

    def current_mix(self) -> Mix:
        return self.controller.model.mix_for_bus(self._current_bus)

    def _build_strips(self) -> None:
        """One strip per input, or one double strip per stereo channel."""
        for strip in self.strips:
            self._row.removeWidget(strip)
            strip.setParent(None)
            strip.deleteLater()
        self.strips = []
        self._strip_for = {}
        linked = self.controller.model.stereo_inputs
        i = 0
        while i < self.device.n_inputs:
            indices = [i, i + 1] if i % 2 == 0 and i // 2 in linked else [i]
            strip = ChannelStrip(indices, self.device)
            strip.edited.connect(self._edited)
            strip.link_toggled.connect(self._link_toggled)
            self._row.insertWidget(len(self.strips), strip)
            self.strips.append(strip)
            for j in indices:
                self._strip_for[j] = strip
            i += len(indices)

    def rebuild(self) -> None:
        """Rebuild the mix selector and strips after the model changed shape."""
        self._build_strips()
        for b in self._group.buttons():
            self._group.removeButton(b)
            self._selector_box.removeWidget(b)
            b.setParent(None)
            b.deleteLater()
        model = self.controller.model
        current = self.current_mix()
        self._mix_buttons = []
        for n, mix in enumerate(model.mixes):
            b = QPushButton(names.mix(mix.buses))
            b.setObjectName("segment")
            b.setProperty("first", n == 0)
            b.setProperty("last", n == len(model.mixes) - 1)
            self._mix_buttons.append((b, mix))
            b.setCheckable(True)
            b.setChecked(mix is current)
            b.clicked.connect(lambda _=False, bus=mix.buses[0]: self.select(bus))
            self._group.addButton(b)
            self._selector_box.addWidget(b)
        self._bind()

    def select(self, bus: int) -> None:
        self._current_bus = bus
        self._bind()

    def _bind(self) -> None:
        mix = self.current_mix()
        self._current_bus = mix.buses[0]
        name = names.mix(mix.buses)
        for s in self.strips:
            s.bind(mix)
        others = [(names.mix(m.buses), m.buses[0]) for m in self.controller.model.mixes
                  if m is not mix]
        self.master.bind(mix, name, others)
        has_partner = (self._current_bus // 2) * 2 + 1 < self.device.n_mixes
        with QSignalBlocker(self.stereo):
            self.stereo.setChecked(mix.stereo)
        self.stereo.setEnabled(has_partner)
        self._update_dests()
        if self._saved:
            self.mark(self._saved)

    def mark(self, saved) -> bool:
        """Outline what is not saved to hardware, in the mix shown; a dot on
        the selector of every mix that holds something unsaved. True if any does."""
        self._saved = saved
        model, mix = self.controller.model, self.current_mix()
        for s in self.strips:
            s.mark(saved, model, mix)
        self.master.mark(saved, mix)
        self.stereo.setUnsaved(saved.mix_shape(mix))
        found = False
        for b, m in getattr(self, "_mix_buttons", []):
            changed = saved.mix(m) or saved.mix_shape(m)
            b.setText(names.mix(m.buses) + (" \u25cf" if changed else ""))
            found = found or changed
        routes = any(saved.control(e) for e in self.device.matrix_inputs)
        links = any(saved.link(model, p) for p in range(self.device.n_inputs // 2))
        return found or routes or links

    def set_meters(self, meters: dict[str, list[float]]) -> None:
        for strip in self.strips:
            strip.set_meters(meters)
        self.master.fader.setLevels([meters["mixes"][b] for b in self.current_mix().buses])

    def _update_dests(self) -> None:
        self.master.sync_send()

    # -- edits ---------------------------------------------------------------

    def _edited(self) -> None:
        self.adopted.clear()
        self.apply(self.current_mix())
        self.model_changed.emit()

    def apply(self, mix: Mix | None = None) -> None:
        self._applying = True
        try:
            self.controller.apply(mix)
        finally:
            self._applying = False

    def _link_toggled(self, pair: int, on: bool) -> None:
        self.controller.model.set_inputs_stereo(pair, on)
        self.apply()
        # Rebuild after this click has finished being delivered to the old strip.
        QTimer.singleShot(0, self.rebuild)
        self.model_changed.emit()

    def _copy_to(self, bus: int) -> None:
        source, target = self.current_mix(), self.controller.model.mix_for_bus(bus)
        self.controller.model.copy_mix(source, target)
        self.apply(target)
        self.message.emit(f"Copied {names.mix(source.buses)} to {names.mix(target.buses)}.")
        self.model_changed.emit()

    def _stereo_toggled(self, on: bool) -> None:
        self.controller.model.set_pair_stereo(self._current_bus // 2, on)
        self._current_bus = (self._current_bus // 2) * 2 if on else self._current_bus
        self.apply()
        self.rebuild()
        self.model_changed.emit()

    def model_replaced(self) -> None:
        """Called after a preset swapped the whole model."""
        self.adopted.clear()
        self.rebuild()

    # -- external changes ----------------------------------------------------

    def _device_changed(self, e) -> None:
        if e.numid in self._route_ids:
            i = self._route_ids[e.numid]
            self._strip_for[i].sync_source(i)
        elif e.numid in self._gain_ids:
            if not self._applying:
                QTimer.singleShot(0, self._check_sync)
        elif e.items:
            self._update_dests()

    def _check_sync(self) -> None:
        if self.controller.in_sync():
            return
        self.controller.adopt_hardware()
        self.adopted.setText("Mixer levels were changed by another program; showing them as mono mixes.")
        self.rebuild()
        self.model_changed.emit()
