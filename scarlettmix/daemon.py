"""Background service: keep the interface's settings across power cycles.

The interface forgets recent changes when it loses power (they are only
stored on request, which the Linux driver cannot do). This service makes up
for that from the computer's side: it remembers the complete state whenever
anything changes it, and puts it back each time the interface appears.

It needs no display and uses only the ALSA controls.
"""

from __future__ import annotations

import select
import signal
import sys
import time

from . import presets
from .alsa import AlsaError
from .backend import AlsaBackend, Backend, find_cards
from .device import Device
from .monitor import Monitor

POLL_SECONDS = 2.0       # how often to look for the interface while it is absent
SETTLE_SECONDS = 1.0     # quiet time after the last change before saving
FOLLOW_SECONDS = 0.2     # how often to look at the monitor knob, when it can be read


def log(text: str) -> None:
    print(f"scarlettmix: {text}", file=sys.stderr, flush=True)


class Session:
    """One connection to the interface, from its appearance to its removal."""

    def __init__(self, backend: Backend, clock=time.monotonic):
        self.device = Device(backend)
        self.clock = clock
        self._dirty_since: float | None = None
        self.monitor = Monitor(self.device)
        self._knob: int | None = None

    def start(self) -> None:
        # Restore before recording anything: after a power cycle the state the
        # interface shows is the stale one, and must not replace the remembered one.
        # Forced: right after the interface appears, the driver's idea of its
        # levels cannot be trusted.
        notes = presets.restore_last(self.device, force=True)
        if notes is None:
            presets.save_last(self.device)
            log("no remembered state yet; remembering the current one")
        else:
            log("restored remembered state" + (f" ({len(notes)} items skipped)" if notes else ""))
            for n in notes:
                log(f"  skipped: {n}")
        if self.monitor.available:
            # The interface knows its own monitor setup (it holds what was
            # last saved); take it from there rather than pushing our note.
            self.monitor.load()
            if self.monitor.adopt_unit():
                self.monitor.save()
            log("monitor buttons read from the unit: " + " ".join(s[0] for s in self.monitor.states))
        self.device.add_listener(self._changed)
        self._fd = self.device.backend.event_fd()

    def _changed(self, elem) -> None:
        if elem.writable:
            self._dirty_since = self.clock()

    def step(self, timeout: float) -> bool:
        """Wait for changes for up to ``timeout`` seconds. False once the interface is gone."""
        if self._dirty_since is not None:
            timeout = min(timeout, max(0.0, self._dirty_since + SETTLE_SECONDS - self.clock()))
        if self.monitor.available:
            timeout = min(timeout, FOLLOW_SECONDS)
        if self._fd is not None:
            select.select([self._fd], [], [], timeout)
        elif timeout:
            time.sleep(timeout)
        if not self.device.poll_events():
            return False
        if self.monitor.available:
            self._follow_knob()
        self.flush()
        return True

    def _follow_knob(self) -> None:
        """Keep blue outputs at the level the front-panel knob asks for."""
        changed = self.monitor.reload_if_changed()
        if changed:
            self.monitor.apply()
        knob = self.device.read_status()[0]
        if changed or knob != self._knob:
            self.monitor.follow(knob)
            self._knob = knob

    def flush(self, force: bool = False) -> None:
        if self._dirty_since is None:
            return
        if force or self.clock() >= self._dirty_since + SETTLE_SECONDS:
            presets.save_last(self.device)
            self._dirty_since = None

    def close(self) -> None:
        try:
            self.flush(force=True)
        except (AlsaError, OSError):
            pass
        self.device.backend.close()


def run() -> int:
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log("waiting for a supported Scarlett")
    while True:
        cards = find_cards()
        if not cards:
            time.sleep(POLL_SECONDS)
            continue
        session = None
        try:
            session = Session(AlsaBackend(cards[0][0]))
            log(f"connected to {session.device.model_name} (card {cards[0][0]})")
            session.start()
            while session.step(60.0):
                pass
            log("interface disconnected; waiting for it to come back")
        except AlsaError as err:
            # Typically the card vanished, or appeared but is not ready yet.
            log(f"lost contact ({err}); retrying")
        finally:
            if session:
                session.close()
        time.sleep(POLL_SECONDS)
