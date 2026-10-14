"""Main window: device header, tabs, presets, and change monitoring."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QSignalBlocker, QSocketNotifier, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QSizePolicy, QStyle,
                               QMessageBox, QPushButton, QSplitter, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import __version__, presets
from ..alsa import AlsaError
from ..backend import AlsaBackend, Backend, find_cards
from ..device import Device
from ..mixer import MixController
from .mixer_page import MixerPage
from ..monitor import Monitor
from ..saved import Saved
from .pages import (CaptureRoutingPanel, InputsPage, LevelStrip, MonitorPanel,
                    OutputRoutingPanel, OutputsPage)
from . import theme
from .widgets import ItemCombo, caption, card

PRESET_FILTER = ("Presets (*.json *.18i20 *.18i8 *.18i6 *.6i6 *.8i6);;"
                 "ScarlettMix presets (*.json);;"
                 "Scarlett MixControl snapshots (*.18i20 *.18i8 *.18i6 *.6i6 *.8i6);;All files (*)")


def _outputs(indices: list[int]) -> str:
    """"output 3", "outputs 7 and 8", "outputs 1, 2, 7 and 8"."""
    n = [str(i + 1) for i in indices]
    if len(n) == 1:
        return f"output {n[0]}"
    return "outputs " + ", ".join(n[:-1]) + " and " + n[-1]


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
        self._revert = QAction("&Revert to Saved\u2026", self, triggered=self.revert_to_saved)
        file_menu.addAction(self._revert)
        file_menu.addSeparator()
        file_menu.addAction(QAction("&Quit", self, shortcut=QKeySequence.Quit,
                                    triggered=self.close))
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(QAction("&About ScarlettMix", self, triggered=self.about))
        self.setWindowIcon(QIcon.fromTheme("audio-card"))

        self._save_timer = QTimer(self, singleShot=True, interval=400)
        self._save_timer.timeout.connect(self._save_state)
        self._meter_timer = QTimer(self, interval=100)
        self._meter_timer.timeout.connect(self._poll_fast)
        self._status_timer = QTimer(self, interval=1000)
        self._status_timer.timeout.connect(self._poll_status)
        self._reconnect_timer = QTimer(self, interval=2000)
        self._reconnect_timer.timeout.connect(self._try_reconnect)
        self._mark_timer = QTimer(self, singleShot=True, interval=40)
        self._mark_timer.timeout.connect(self._update_saved)

        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        bar.messageChanged.connect(lambda text: bar.setVisible(bool(text)))
        bar.hide()
        self.load_backend(backend)
        self.fit_to_contents()

    # -- device lifecycle ----------------------------------------------------

    def load_backend(self, backend: Backend) -> None:
        self.device = device = Device(backend)
        # After the interface powers on, the driver misreports its levels until
        # they have all been sent again. Never show or record those readings as
        # the truth: show what was last saved to the unit instead, if we know it.
        self._track = not backend.simulated or getattr(backend, "honour_trust", False)
        self._snapshot = None
        self._saved = False             # record of the last save; False = not read yet
        self.saved = None               # comparison against that record
        self.unsaved = False
        self._demo_saved = None         # the same for the simulated device, kept in memory
        if self._track:
            self._snapshot = presets.prepare(device)
        if self._snapshot:
            saved = self._snapshot.get("mixer")
        else:
            saved = presets.load_state(device) if self._track else None
        self.controller = MixController(device, saved)
        self.setWindowTitle(f"ScarlettMix — {device.model_name}"
                            + (" (simulated)" if backend.simulated else ""))

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(theme.S3, theme.S2, theme.S3, theme.S2)
        root.setSpacing(theme.S2)
        root.addLayout(self._header())
        root.addWidget(self._banner())
        self.restart_banner, self.restart_text = self._message_bar(
            "noticeBar", QStyle.SP_MessageBoxInformation)
        self.restart_banner.hide()
        root.addWidget(self.restart_banner)

        # Same arrangement as the original: mixer on top, routing below it on
        # the left, monitor section on the right.
        self.mixer_page = MixerPage(device, self.controller)
        self.mixer_page.model_changed.connect(self._save_timer.start)
        self.mixer_page.model_changed.connect(self._mark_timer.start)
        self.mixer_page.message.connect(lambda text: self.statusBar().showMessage(text, 5000))

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.routing_page = OutputRoutingPanel(device)
        self.outputs_page = OutputsPage(device)
        self._tab_pages = [(self.routing_page, "Routing"), (CaptureRoutingPanel(device), "DAW Inputs"),
                           (self.outputs_page, "Output Levels")]
        if device.input_switches:
            self._tab_pages.append((InputsPage(device), "Inputs"))
        for page, title in self._tab_pages:
            self.tabs.addTab(page, title)
        # Each page's own action (a preset menu, a reset) sits in the tab bar's corner.
        corner = QWidget()
        corner_lay = QHBoxLayout(corner)
        corner_lay.setContentsMargins(0, 0, 0, theme.S1)
        self._corners = []
        for page, _ in self._tab_pages:
            button = getattr(page, "corner", None)
            if button:
                corner_lay.addWidget(button)
            self._corners.append(button)
        self.tabs.setCornerWidget(corner, Qt.TopRightCorner)
        self.tabs.currentChanged.connect(self._show_corner)
        self._show_corner(0)

        monitor = card()
        monitor.setFixedWidth(236)
        mon_lay = QVBoxLayout(monitor)
        mon_lay.setContentsMargins(theme.S3, theme.S2, theme.S3, theme.S3)
        self.monitor = Monitor(device)
        self.monitor_panel = None
        if self.monitor.available:
            # Drawn from the interface; the monitor object keeps nothing itself.
            self.monitor_panel = MonitorPanel(device, self.monitor)
            self.monitor_panel.changed.connect(self._mark_timer.start)
            self.monitor_panel.message.connect(lambda t: self.statusBar().showMessage(t, 12000))
            mon_lay.addWidget(self.monitor_panel)
            mon_lay.addStretch(1)
        self.master_strip = LevelStrip(device, "Master", device.master_volume, device.master_switch,
                                       bold=True, listen=True)
        self.master_strip.setFixedWidth(112)
        self._update_saved()

        # Same arrangement as the original: the mixer across the top; below
        # it the routing tabs, the monitor section and the master level.
        lower = QWidget()
        lower_lay = QHBoxLayout(lower)
        lower_lay.setContentsMargins(0, 0, 0, 0)
        lower_lay.setSpacing(theme.S3)
        lower_lay.addWidget(self.tabs, 1)
        if self.monitor_panel:
            lower_lay.addWidget(monitor)
        lower_lay.addWidget(self.master_strip)
        self._lower = lower
        left = QSplitter(Qt.Vertical)
        left.addWidget(self.mixer_page)
        left.addWidget(lower)
        left.setStretchFactor(0, 1)
        left.setStretchFactor(1, 0)
        left.setChildrenCollapsible(False)
        root.addWidget(left, 1)
        self.setCentralWidget(central)
        # Start with nothing focused, rather than a focus ring on the first selector.
        central.setFocusPolicy(Qt.ClickFocus)
        central.setFocus()
        # The routing area gets the height the routing grid needs, the mixer
        # the rest (the other tabs scroll or shrink within that height).
        self._left = left
        QTimer.singleShot(0, self._size_routing_area)

        device.add_listener(self._device_changed)
        fd = backend.event_fd()
        if fd is not None:
            self._notifier = QSocketNotifier(fd, QSocketNotifier.Read, self)
            self._notifier.activated.connect(self._events_ready)
        self._open.setEnabled(True)
        self._save.setEnabled(True)
        self._status_timer.start()
        for act in (self._save_hw, self._revert):
            act.setEnabled(bool(device.hw_save))
            act.setVisible(bool(device.hw_save))
        if device.has_meters or self.monitor_panel:
            self._meter_timer.start()
        self._poll_status()
        if backend.simulated:
            self.statusBar().showMessage("Simulated device: nothing here reaches real hardware.")


    def default_width(self) -> int:
        """As wide as the row below the mixer needs (routing panels, monitor
        section, master level), so it opens without blank space; the mixer
        strips scroll sideways, and widening the window shows more of them."""
        lay = self.centralWidget().layout()
        margins = lay.contentsMargins().left() + lay.contentsMargins().right()
        row = self.routing_page.sizeHint().width() + theme.S3 + self.master_strip.width()
        if self.monitor_panel:
            row += 236 + theme.S3
        return row + margins

    def fit_to_contents(self) -> None:
        """Open tall enough for the routing rows without scrolling, and as wide
        as default_width(), within the screen."""
        want = self.sizeHint()
        want.setWidth(min(want.width(), self.default_width()))
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen and screen.width() > 0:
            want = want.boundedTo(screen.size() * 0.94)
        self.resize(want)

    def _message_bar(self, kind: str, icon) -> tuple[QFrame, QLabel]:
        """A one-line message across the window: icon, text, optional button."""
        bar = QFrame()
        bar.setObjectName(kind)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(theme.S2, theme.S1, theme.S1, theme.S1)
        lay.setSpacing(theme.S2)
        pic = QLabel()
        pic.setPixmap(self.style().standardIcon(icon).pixmap(16, 16))
        lay.addWidget(pic)
        text = QLabel()
        text.setWordWrap(True)
        lay.addWidget(text, 1)
        return bar, text

    def _banner(self) -> QFrame:
        """Shown while the driver's level readings cannot be believed."""
        self.banner, text = self._message_bar("warnBar", QStyle.SP_MessageBoxWarning)
        self.sync_button = QPushButton("Sync")
        self.sync_button.setToolTip("Send every level and switch shown here to the interface, "
                                    "so that it and the driver agree")
        self.sync_button.clicked.connect(self.sync_to_unit)
        self.banner.layout().addWidget(self.sync_button)
        self.measure_button = QPushButton("Read mixer")
        self.measure_button.setToolTip("Measure the mixer gains the interface is really using, with "
                                       "its own level meters (about 15 seconds; outputs that play "
                                       "mixes are muted meanwhile)")
        self.measure_button.clicked.connect(self.read_mixer)
        self.measure_button.setVisible(bool(self.device.has_meters))
        self.banner.layout().insertWidget(self.banner.layout().count() - 1, self.measure_button)
        self.banner_text = text
        if self._snapshot:
            text.setText("Interface restarted: showing your last saved setup, as levels can't "
                         "be read back.")
            text.setToolTip("After the interface or the computer restarts, the driver cannot "
                            "read levels, gains and mutes back, so the setup from your last Save "
                            "to Hardware is shown. Sync sends it to make sure the interface "
                            "matches.")
        else:
            text.setText("Interface restarted: levels and mutes shown may be wrong.")
            text.setToolTip("The driver cannot read levels, gains and mutes back after a restart "
                            "and there is no saved setup to show instead. Anything you change is "
                            "sent. Loading a preset, or Save to Hardware once things are as you "
                            "want them, puts this right.")
            self.sync_button.hide()     # never push readings we know to be unreliable
        self.banner.setVisible(self.device.untrusted)
        return self.banner

    def _size_routing_area(self) -> None:
        want = self.routing_page.sizeHint().height() + self.tabs.tabBar().sizeHint().height() + theme.S2
        if self.monitor_panel:
            want = max(want, self.monitor_panel.sizeHint().height() + theme.S2 + theme.S3 + 4)
        total = sum(self._left.sizes())
        self._left.setSizes([max(1, total - want), want])

    def read_mixer(self) -> None:
        """Measure the real mixer gains with the meters and show them."""
        from ..measure import MeasureError, measure_gains, pipewire_player, simulated_player
        if QMessageBox.question(
                self, "Read mixer",
                "Measure the mixer from the interface?\n\nA quiet test tone is played on a free "
                "DAW channel for about 15 seconds. Outputs that play mixes (such as the headphones) "
                "are muted meanwhile; everything is put back afterwards.") != QMessageBox.Yes:
            return
        backend = self.device.backend
        try:
            play = simulated_player(backend) if backend.simulated else pipewire_player(backend.card)
        except MeasureError as err:
            QMessageBox.warning(self, "Read mixer", f"Cannot measure the mixer:\n{err}")
            return

        def wait(seconds: float) -> None:           # keep the window responsive
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                QApplication.processEvents()
                time.sleep(0.02)

        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.statusBar().showMessage("Measuring the mixer\u2026")
        self.mixer_page._applying = True
        try:
            levels = measure_gains(self.device, play, sleep=wait)
        except MeasureError as err:
            QMessageBox.warning(self, "Read mixer", f"Could not measure the mixer:\n{err}")
            return
        except AlsaError:
            self._disconnected()
            return
        finally:
            self.mixer_page._applying = False
            QApplication.restoreOverrideCursor()
            self.statusBar().clearMessage()
        for key, level in levels.items():
            self.device.believe(self.device.matrix_gain[key], [level])
        # Keep the saved mixer layout (stereo pairs, pans) if it is exactly what
        # was measured; otherwise show the measured gains as they are.
        saved = self._snapshot.get("mixer") if self._snapshot else None
        if not (saved and self.controller.in_sync()):
            self.controller.adopt_hardware()
        self.mixer_page.model_replaced()
        self.banner_text.setText("Interface restarted: mixer read from the interface; output "
                                 "levels show your last saved setup.")
        self.measure_button.hide()
        self.device.poll_events()               # the restored mutes and routing, before comparing
        self._update_saved()
        self.statusBar().showMessage("Mixer measured from the interface.", 6000)

    def _show_corner(self, index: int) -> None:
        for i, button in enumerate(self._corners):
            if button:
                button.setVisible(i == index)

    def _now_trusted(self) -> None:
        self.device.untrusted = False
        if self._track:
            presets.mark_synced(self.device)
        self.banner.hide()

    def sync_to_unit(self) -> None:
        try:
            self.mixer_page._applying = True
            try:
                count = presets.sync_all(self.device)
            finally:
                self.mixer_page._applying = False
        except AlsaError:
            self._disconnected()
            return
        self._now_trusted()
        self.statusBar().showMessage(f"Sent {count} settings to the interface.", 5000)

    def _header(self) -> QHBoxLayout:
        d = self.device
        bar = QHBoxLayout()
        bar.setSpacing(theme.S2)
        bar.setAlignment(Qt.AlignVCenter)
        name = QLabel(d.model_name)
        name.setObjectName("deviceName")
        font = name.font()
        font.setPointSizeF(font.pointSizeF() * 1.25)
        name.setFont(font)
        bar.addWidget(name)
        bar.addSpacing(theme.S4)

        self.clock = None
        if d.clock_source:
            bar.addWidget(caption("Clock"))
            self.clock = ItemCombo(d.clock_source.items)
            self.clock.setAccessibleName("Clock source")
            self.clock.setCurrentIndex(d.get(d.clock_source)[0])
            self.clock.currentIndexChanged.connect(lambda i: d.set(d.clock_source, [i]))
            bar.addWidget(self.clock)
        chip = QFrame()
        chip.setObjectName("chip")
        chip_lay = QHBoxLayout(chip)
        chip_lay.setContentsMargins(theme.S3, 0, theme.S3, 0)
        chip_lay.setSpacing(theme.S1 + 2)
        self.sync = QLabel()
        chip_lay.addWidget(self.sync)
        chip_lay.addWidget(caption("\u00b7"))
        self.rate = QLabel()
        self.rate.setToolTip("Sample rate of the running stream. On Linux this is chosen by the "
                             "audio server (PipeWire/JACK) or the application, not by the mixer.")
        chip_lay.addWidget(self.rate)
        bar.addSpacing(theme.S1)
        bar.addWidget(chip)
        bar.addStretch(1)
        self.saved_label = QLabel()
        self.revert_button = QPushButton("Revert")
        self.revert_button.setObjectName("flat")
        self.revert_button.setToolTip("Put the interface back to the setup it has stored, "
                                      "dropping every change that is not saved")
        self.revert_button.clicked.connect(self.revert_to_saved)
        self.save_button = QPushButton("Save to Hardware")
        self.save_button.setObjectName("primary")
        self.save_button.setToolTip("Store the running setup in the interface, so it starts up "
                                    "this way with or without the computer")
        self.save_button.clicked.connect(self.save_to_hardware)
        for w in (self.saved_label, self.revert_button, self.save_button):
            w.setVisible(bool(d.hw_save))
            bar.addWidget(w)
        bar.insertSpacing(bar.indexOf(self.revert_button), theme.S2)
        return bar

    def _update_saved(self) -> None:
        """Outline every control that is not saved to hardware, and say in the
        top bar whether anything is."""
        if not self.device or not self.device.hw_save:
            return
        record = self._saved_record()
        if self.saved is None or self.saved.record is not record:
            self.saved = Saved(self.device, record)
        saved = self.saved
        if self.monitor_panel:
            saved.own_levels = self.monitor.own_levels()
            self.outputs_page.set_on_knob(self.monitor.knob_controlled())
        self.unsaved = False
        if self.mixer_page.mark(saved):
            self.unsaved = True
        for i, (page, title) in enumerate(self._tab_pages):
            changed = page.mark(saved)
            self.tabs.setTabText(i, title + (" \u25cf" if changed else ""))
            self.unsaved = self.unsaved or changed
        if self.monitor_panel and self.monitor_panel.mark(saved):
            self.unsaved = True
        if self.master_strip.mark(saved):
            self.unsaved = True
        if self.clock:
            clock = saved.control(self.device.clock_source)
            self.clock.setUnsaved(clock)
            self.unsaved = self.unsaved or clock
        self.unsaved = self.unsaved or saved.any(self.monitor)
        self.revert_button.setEnabled(saved.known and self.unsaved)
        self._revert.setEnabled(saved.known and self.unsaved)

        # Saved, but the interface has not been switched off and on since: the
        # monitor knob only takes up its outputs when it starts.
        same_power = bool(record) and record.get("connection") == self.device.backend.connection_id() != ""
        waiting = saved.waiting_for_restart(self.monitor) if self.monitor_panel else []
        restart = saved.known and (same_power or bool(waiting))
        self.restart_banner.setVisible(restart)
        if restart:
            self.restart_text.setText(
                "Saved to hardware \u2013 now switch the interface off and on to finish."
                + (f" Until then its monitor knob does not control {_outputs(waiting)}."
                   if waiting else ""))
        if not saved.known:
            text, color, tip = ("Not yet saved from ScarlettMix", theme.colors()["muted"],
                                "ScarlettMix has no record of what the interface has stored, so "
                                "it cannot show what is saved. Save to Hardware once to start "
                                "tracking changes.")
        elif self.unsaved:
            text, color, tip = ("\u25cf Not saved", theme.UNSAVED,
                                "Everything drawn as an outline is lost when the interface is "
                                "switched off, unless you Save to Hardware.")
        elif restart:
            text, color, tip = ("\u2713 Saved \u2013 restart the interface", theme.UNSAVED,
                                "Everything is stored. Switch the interface off and on to finish.")
        else:
            text, color, tip = ("\u2713 Saved to hardware", theme.OK,
                                "The running setup is what the interface has stored.")
        self.saved_label.setText(text)
        self.saved_label.setStyleSheet(f"color: {color};")
        self.saved_label.setToolTip(tip)

    def _saved_record(self) -> dict | None:
        if not self._track:
            return self._demo_saved
        if self._saved is False:
            self._saved = presets.load_saved(self.device)
        return self._saved

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
        """Meters and front panel, ten times a second."""
        d = self.device
        if not d:
            return
        shown = self.isVisible() and not self.isMinimized()
        try:
            if d.has_meters and shown:
                self.mixer_page.set_meters(d.read_meters())
            # The knob is still followed with the window out of sight.
            if self.monitor_panel and (shown or self.monitor.pending()):
                self.monitor_panel.set_status(*d.read_status())
        except AlsaError:
            self._disconnected()

    def save_to_hardware(self, confirm: bool = True) -> bool:
        """Store the running setup in the interface. Returns False if it was
        not saved."""
        if confirm and QMessageBox.question(
                self, "Save to hardware",
                "Store the current routing, mixer, levels and monitor section in the interface, "
                "replacing what it has stored now?\n\nIt will then start up this way without "
                "the computer.\n\nAFTER SAVING, SWITCH THE INTERFACE OFF AND ON.") != QMessageBox.Yes:
            return False
        try:
            if self.monitor_panel:
                # Also writes the knob assignment into the saved copy.
                self.monitor.save_to_hardware()
            else:
                self.device.save_to_hardware()
        except AlsaError as err:
            QMessageBox.warning(self, "Save to hardware", f"The interface refused:\n{err}")
            return False
        if not self._track:
            self._demo_saved = presets.saved_record(self.device, self.controller.model, self.monitor)
        # Record what the unit now holds. Only when the values shown are known
        # to be real: readings from a freshly restarted unit are not.
        if self._track and (not self.device.untrusted or self._snapshot):
            try:
                presets.save_saved(self.device, self.controller.model, self.monitor)
                self._snapshot = self._saved = presets.load_saved(self.device)
            except OSError as err:
                self.statusBar().showMessage(f"Could not record the saved setup: {err}", 8000)
        self._update_saved()
        QMessageBox.warning(
            self, "Now restart the interface",
            "Saved.\n\nNow switch the interface off and on.\n\nThe monitor knob only takes up "
            "its outputs when the interface starts, so a save is not finished until then.")
        return True

    def revert_to_saved(self, confirm: bool = True) -> bool:
        """Put the interface back to its saved setup, dropping unsaved changes."""
        if not self.saved or not self.saved.known:
            return False
        if confirm and QMessageBox.question(
                self, "Revert to saved",
                "Put the interface back to the setup it has stored?\n\nEvery change that is "
                "not saved (drawn as an outline) is lost.") != QMessageBox.Yes:
            return False
        try:
            self.mixer_page._applying = True
            try:
                notes = self.saved.revert(self.controller, self.monitor)
            finally:
                self.mixer_page._applying = False
        except AlsaError:
            self._disconnected()
            return False
        self.mixer_page.model_replaced()
        if self.monitor_panel:
            if self.monitor_panel._knob is not None:
                self.monitor.follow(self.monitor_panel._knob)
            self.monitor_panel.sync()
        self._save_timer.start()
        self._update_saved()
        self.statusBar().showMessage("Back to the saved setup." + (" " + "; ".join(notes) if notes else ""),
                                     8000)
        return True

    def _poll_status(self) -> None:
        d = self.device
        if not d:
            return
        try:
            if d.sync_status:
                d.refresh(d.sync_status)
                locked = d.item(d.sync_status) == "Locked"
                self.sync.setText("● Locked" if locked else "● No lock")
                self.sync.setStyleSheet(f"color: {theme.OK if locked else theme.MUTE};")
            rate = d.backend.sample_rate()
        except AlsaError:
            self._disconnected()
            return
        self.rate.setText(f"{rate / 1000:g} kHz" if rate else "idle")
        try:
            if self.monitor_panel:
                self.monitor_panel.refresh()        # reads only
            self._update_saved()
        except AlsaError:
            self._disconnected()

    def _device_changed(self, e) -> None:
        d = self.device
        if e.writable:
            self._save_timer.start()
            self._mark_timer.start()
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
                notes += presets.apply(preset, self.device, self.controller,
                                       force=self.device.untrusted)
                if self.monitor_panel and "monitor" in preset:
                    if self.monitor.fits(preset["monitor"]):
                        self.monitor.apply(preset["monitor"])
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
        self._update_saved()
        if self.device.untrusted and "mixer" in preset and len(preset.get("controls", {})) > 20:
            # A complete preset has just been sent in full, forced: the driver
            # and the interface agree again.
            self._now_trusted()
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

    def about(self) -> None:
        QMessageBox.about(
            self, "About ScarlettMix",
            f"<h3>ScarlettMix {__version__}</h3>"
            "<p>Mixer and routing control for first-generation Focusrite Scarlett "
            "interfaces on Linux, modelled on the discontinued Scarlett MixControl.</p>"
            "<p>Licensed under the GNU GPL, version 3 or later.<br>"
            '<a href="https://github.com/tucktuckg00se/ScarlettMix">'
            "github.com/tucktuckg00se/ScarlettMix</a></p>"
            "<p>Not affiliated with Focusrite.</p>")

    def _ask_close(self) -> str:
        """Unsaved changes at close: "save", "discard" or "cancel"."""
        box = QMessageBox(QMessageBox.Warning, "Unsaved changes",
                          "Some settings are not saved to hardware.", parent=self)
        box.setInformativeText("Save stores them in the interface. Discard puts the interface "
                               "back to its saved setup, so the sound changes back now.")
        save = box.addButton("Save to Hardware", QMessageBox.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        return "save" if clicked is save else "discard" if clicked is discard else "cancel"

    def closeEvent(self, ev) -> None:
        if self.device and self.device.hw_save and self.saved and self.saved.known:
            try:
                self._update_saved()
            except AlsaError:
                self.unsaved = False
            if self.unsaved:
                answer = self._ask_close()
                done = (self.save_to_hardware(confirm=False) if answer == "save"
                        else self.revert_to_saved(confirm=False) if answer == "discard" else False)
                if not done and self.device:
                    ev.ignore()
                    return
        if self.device and self.monitor_panel:
            try:
                # Outputs this program was moving with the knob go back to
                # their own level; nothing will be moving them once it is gone.
                self.monitor.stop_following()
            except AlsaError:
                pass
        self._save_state()
        self._teardown()
        super().closeEvent(ev)
