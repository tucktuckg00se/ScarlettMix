"""Main window: device header, tabs, presets, and change monitoring."""

from __future__ import annotations

import os

from PySide6.QtCore import QSignalBlocker, QSocketNotifier, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from .. import presets
from ..alsa import AlsaError
from ..backend import AlsaBackend, Backend, find_cards
from ..device import Device
from ..mixer import MixController
from .mixer_page import MixerPage
from ..monitor import Monitor
from .pages import (CaptureRoutingPanel, InputsPage, LevelStrip, MonitorPanel,
                    OutputRoutingPanel, OutputsPage)
from .widgets import ItemCombo

PRESET_FILTER = ("Presets (*.json *.18i20 *.18i8 *.18i6 *.6i6 *.8i6);;"
                 "ScarlettMix presets (*.json);;"
                 "Scarlett MixControl snapshots (*.18i20 *.18i8 *.18i6 *.6i6 *.8i6);;All files (*)")


class MainWindow(QMainWindow):
    def __init__(self, backend: Backend):
        super().__init__()
        self.device: Device | None = None
        self._notifier: QSocketNotifier | None = None
        self._last_dir = os.path.expanduser("~")

        file_menu = self.menuBar().addMenu("&File")
        self._open = QAction("&Open preset…", self, shortcut=QKeySequence.Open,
                             triggered=self.open_preset)
        self._save = QAction("&Save preset as…", self, shortcut=QKeySequence.Save,
                             triggered=self.save_preset)
        file_menu.addAction(self._open)
        file_menu.addAction(self._save)
        file_menu.addSeparator()
        self._save_hw = QAction("Save to &Hardware\u2026", self, triggered=self.save_to_hardware)
        file_menu.addAction(self._save_hw)
        file_menu.addSeparator()
        file_menu.addAction(QAction("&Quit", self, shortcut=QKeySequence.Quit,
                                    triggered=self.close))

        self._save_timer = QTimer(self, singleShot=True, interval=400)
        self._save_timer.timeout.connect(self._save_state)
        self._meter_timer = QTimer(self, interval=100)
        self._meter_timer.timeout.connect(self._poll_fast)
        self._status_timer = QTimer(self, interval=1000)
        self._status_timer.timeout.connect(self._poll_status)
        self._reconnect_timer = QTimer(self, interval=2000)
        self._reconnect_timer.timeout.connect(self._try_reconnect)

        self.load_backend(backend)
        self.fit_to_contents()

    # -- device lifecycle ----------------------------------------------------

    def load_backend(self, backend: Backend) -> None:
        self.device = device = Device(backend)
        saved = None if backend.simulated else presets.load_state(device)
        self.controller = MixController(device, saved)
        self.setWindowTitle(f"ScarlettMix — {device.model_name}"
                            + (" (simulated)" if backend.simulated else ""))

        central = QWidget()
        root = QVBoxLayout(central)
        root.addLayout(self._header())

        # Same arrangement as the original: mixer on top, routing below it on
        # the left, monitor section on the right.
        self.mixer_page = MixerPage(device, self.controller)
        self.mixer_page.model_changed.connect(self._save_timer.start)
        self.mixer_page.message.connect(lambda text: self.statusBar().showMessage(text, 5000))

        self.tabs = QTabWidget()
        self.routing_page = OutputRoutingPanel(device)
        self.tabs.addTab(self.routing_page, "Routing")
        self.tabs.addTab(CaptureRoutingPanel(device), "DAW Inputs")
        self.tabs.addTab(OutputsPage(device), "Output Levels")
        if device.input_switches:
            self.tabs.addTab(InputsPage(device), "Inputs")

        monitor = QGroupBox("Monitor")
        mon_lay = QVBoxLayout(monitor)
        self.monitor = Monitor(device)
        self.monitor_panel = None
        if self.monitor.available:
            # Button states are ours to remember; the unit cannot report them.
            self.monitor.load()
            # The unit is the truth about its monitor setup; this only reads.
            if self.monitor.adopt_unit() and not backend.simulated:
                self.monitor.save()
            self.monitor_panel = MonitorPanel(device, self.monitor)
            mon_lay.addWidget(self.monitor_panel)
        mon_lay.addWidget(LevelStrip(device, "Master", device.master_volume, device.master_switch,
                                     bold=True, listen=True), 1)

        lower = QWidget()
        lower_lay = QHBoxLayout(lower)
        lower_lay.setContentsMargins(0, 0, 0, 0)
        lower_lay.addWidget(self.tabs, 1)
        lower_lay.addWidget(monitor)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.mixer_page)
        split.addWidget(lower)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setChildrenCollapsible(False)
        root.addWidget(split, 1)
        self.setCentralWidget(central)

        device.add_listener(self._device_changed)
        fd = backend.event_fd()
        if fd is not None:
            self._notifier = QSocketNotifier(fd, QSocketNotifier.Read, self)
            self._notifier.activated.connect(self._events_ready)
        self._open.setEnabled(True)
        self._save.setEnabled(True)
        self._status_timer.start()
        self._save_hw.setEnabled(bool(device.hw_save))
        self._save_hw.setVisible(bool(device.hw_save))
        if device.has_meters or self.monitor_panel:
            self._meter_timer.start()
        self._poll_status()
        if backend.simulated:
            self.statusBar().showMessage("Simulated device: nothing here reaches real hardware.")
        elif self.controller.restored:
            self.statusBar().showMessage("Restored mixer layout from the last session.", 5000)
        else:
            self.statusBar().showMessage("Read current settings from the device.", 5000)

    def fit_to_contents(self) -> None:
        """Open large enough to show every strip and routing row without
        scrolling, at whatever font size the desktop uses, within the screen."""
        want = self.sizeHint()
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen and screen.width() > 0:
            want = want.boundedTo(screen.size() * 0.94)
        self.resize(want)

    def _header(self) -> QHBoxLayout:
        d = self.device
        bar = QHBoxLayout()
        name = QLabel(d.model_name)
        font = name.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.15)
        name.setFont(font)
        bar.addWidget(name)
        bar.addSpacing(20)

        self.clock = None
        if d.clock_source:
            bar.addWidget(QLabel("Clock source:"))
            self.clock = ItemCombo(d.clock_source.items)
            self.clock.setAccessibleName("Clock source")
            self.clock.setCurrentIndex(d.get(d.clock_source)[0])
            self.clock.currentIndexChanged.connect(lambda i: d.set(d.clock_source, [i]))
            bar.addWidget(self.clock)
        self.sync = QLabel()
        bar.addWidget(self.sync)
        bar.addSpacing(12)
        self.rate = QLabel()
        self.rate.setToolTip("Sample rate of the running stream. On Linux this is chosen by the "
                             "audio server (PipeWire/JACK) or the application, not by the mixer.")
        bar.addWidget(self.rate)
        bar.addStretch(1)
        return bar

    def _teardown(self) -> None:
        self._status_timer.stop()
        self._meter_timer.stop()
        if self._notifier:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        if self.device:
            self.device.backend.close()
            self.device = None

    def _disconnected(self) -> None:
        self._save_state()
        self._teardown()
        placeholder = QLabel("The interface was disconnected.\nWaiting for it to come back…")
        placeholder.setAlignment(Qt.AlignCenter)
        self.setCentralWidget(placeholder)
        self._open.setEnabled(False)
        self._save.setEnabled(False)
        self.statusBar().showMessage("Device disconnected.")
        self._reconnect_timer.start()

    def _try_reconnect(self) -> None:
        cards = find_cards()
        if not cards:
            return
        try:
            backend = AlsaBackend(cards[0][0])
        except AlsaError:
            return
        self._reconnect_timer.stop()
        self.load_backend(backend)

    # -- monitoring ----------------------------------------------------------

    def _events_ready(self) -> None:
        if self.device and not self.device.poll_events():
            self._disconnected()

    def _poll_fast(self) -> None:
        """Meters and front panel, ten times a second, only while visible."""
        d = self.device
        if not d or not self.isVisible() or self.isMinimized():
            return
        try:
            if d.has_meters:
                self.mixer_page.set_meters(d.read_meters())
            if self.monitor_panel:
                self.monitor_panel.set_status(*d.read_status())
        except AlsaError:
            self._disconnected()

    def save_to_hardware(self) -> None:
        if QMessageBox.question(
                self, "Save to hardware",
                "Store the current routing and mixer in the interface, replacing what it "
                "has stored now?\n\nIt will then start up this way without the computer."
        ) != QMessageBox.Yes:
            return
        try:
            restart_needed = False
            if self.monitor_panel:
                # Also writes the knob assignment into the saved copy.
                restart_needed = self.monitor.save_to_hardware()
                self.monitor_panel.saved()
            else:
                self.device.save_to_hardware()
        except AlsaError as err:
            QMessageBox.warning(self, "Save to hardware", f"The interface refused:\n{err}")
            return
        if restart_needed:
            QMessageBox.information(
                self, "Save to hardware",
                "Saved.\n\nThe outputs marked * will follow the monitor knob by themselves "
                "once the interface has been switched off and on. Until then this program "
                "follows the knob for them.")
        self.statusBar().showMessage("Saved to the interface.", 5000)

    def _poll_status(self) -> None:
        d = self.device
        if not d:
            return
        try:
            if d.sync_status:
                d.refresh(d.sync_status)
                locked = d.item(d.sync_status) == "Locked"
                self.sync.setText("● Locked" if locked else "● No lock")
                self.sync.setStyleSheet(f"color: {'#3fa34d' if locked else '#d9534f'};")
            rate = d.backend.sample_rate()
        except AlsaError:
            self._disconnected()
            return
        self.rate.setText(f"{rate / 1000:g} kHz" if rate else "idle")

    def _device_changed(self, e) -> None:
        d = self.device
        if e.writable:
            self._save_timer.start()
        if d and self.clock and d.clock_source and e.numid == d.clock_source.numid:
            with QSignalBlocker(self.clock):
                self.clock.setCurrentIndex(d.get(e)[0])

    # -- presets and state ---------------------------------------------------

    def _save_state(self) -> None:
        if self.device and not self.device.backend.simulated:
            try:
                presets.save_state(self.device, self.controller.model)
            except OSError as err:
                self.statusBar().showMessage(f"Could not save mixer layout: {err}", 8000)

    def open_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open preset", self._last_dir, PRESET_FILTER)
        if path:
            self.load_preset_file(path)

    def load_preset_file(self, path: str) -> bool:
        self._last_dir = os.path.dirname(path)
        try:
            preset, notes = presets.load_file(path, self.device)
            self.mixer_page._applying = True
            try:
                notes += presets.apply(preset, self.device, self.controller)
                if self.monitor_panel and "monitor" in preset:
                    if self.monitor.set_states(preset["monitor"]):
                        self.monitor.apply()
                        self.monitor.save()
                        self.monitor_panel.sync()
                    else:
                        notes.append("monitor buttons: do not fit this device")
            finally:
                self.mixer_page._applying = False
        except AlsaError:
            self._disconnected()
            return False
        except Exception as err:  # malformed files come in many shapes
            QMessageBox.warning(self, "Open preset", f"Could not load {os.path.basename(path)}:\n{err}")
            return False
        self.mixer_page.model_replaced()
        self._save_timer.start()
        self.statusBar().showMessage(f"Loaded {os.path.basename(path)}.", 5000)
        if notes:
            QMessageBox.information(self, "Preset loaded",
                                    "Loaded, with these exceptions:\n\n• " + "\n• ".join(notes))
        return True

    def save_preset(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save preset", self._last_dir,
                                              "ScarlettMix presets (*.json)")
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        self._last_dir = os.path.dirname(path)
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            preset = presets.snapshot(self.device, self.controller.model, name)
            if self.monitor_panel:
                preset["monitor"] = list(self.monitor.states)
            presets.save_file(path, preset)
        except OSError as err:
            QMessageBox.warning(self, "Save preset", f"Could not save:\n{err}")
            return
        self.statusBar().showMessage(f"Saved {os.path.basename(path)}.", 5000)

    def closeEvent(self, ev) -> None:
        self._save_state()
        self._teardown()
        super().closeEvent(ev)
