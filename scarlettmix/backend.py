"""Control backends: the real ALSA card, and an in-memory simulation.

Both expose the same small interface so the device model and UI never
care which one they are talking to.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable

from . import alsa
from .alsa import TYPE_BOOLEAN, TYPE_ENUMERATED, TYPE_INTEGER, ElemInfo

# First-generation Scarletts handled by the kernel's mixer_scarlett driver.
SUPPORTED_USB_IDS = {
    "1235:8002": "Scarlett 8i6",
    "1235:8004": "Scarlett 18i6",
    "1235:800c": "Scarlett 18i20",
    "1235:8012": "Scarlett 6i6",
    "1235:8014": "Scarlett 18i8",
}

FIXTURE_18I20 = os.path.join(os.path.dirname(__file__), "data", "scarlett18i20.json")


class Backend:
    """Interface shared by AlsaBackend and FakeBackend."""

    name = ""
    usb_id = ""
    simulated = False

    def elements(self) -> list[ElemInfo]:
        raise NotImplementedError

    def read(self, e: ElemInfo) -> list[int]:
        raise NotImplementedError

    def write(self, e: ElemInfo, values: list[int]) -> None:
        raise NotImplementedError

    def event_fd(self) -> int | None:
        """File descriptor that becomes readable on external changes, if any."""
        return None

    def drain_events(self) -> tuple[set[int], bool]:
        return set(), False

    def sample_rate(self) -> int | None:
        return None

    def connection_id(self) -> str:
        """Changes every time the interface re-appears (power cycle, replug)."""
        return ""

    def close(self) -> None:
        pass


class AlsaBackend(Backend):
    def __init__(self, card: int):
        self.card = card
        self.usb_id = alsa._read(f"/proc/asound/card{card}/usbid")
        self.name = alsa._card_name(card)
        self._ctl = alsa.Ctl(card)

    def elements(self):
        return self._ctl.elements()

    def read(self, e):
        return self._ctl.read(e)

    def write(self, e, values):
        self._ctl.write(e, values)

    def event_fd(self):
        return self._ctl.subscribe()

    def drain_events(self):
        return self._ctl.drain_events()

    def connection_id(self):
        # New whenever the driver has read the unit afresh: the USB device
        # number changes on a power cycle or replug, the sysfs node on a
        # driver reload, the boot id on a restart of the computer.
        try:
            node = os.stat(f"/sys/class/sound/card{self.card}").st_ino
        except OSError:
            return ""
        return "/".join((alsa._read("/proc/sys/kernel/random/boot_id").strip(),
                         alsa._read(f"/proc/asound/card{self.card}/usbbus").strip(), str(node)))

    def sample_rate(self):
        # The kernel reports the measured rate of whichever stream is running.
        text = alsa._read(f"/proc/asound/card{self.card}/stream0")
        m = re.search(r"Momentary freq = (\d+) Hz", text)
        if not m:
            return None
        measured = int(m.group(1))
        return min((44100, 48000, 88200, 96000, 176400, 192000), key=lambda r: abs(r - measured))

    def close(self):
        self._ctl.close()


class FakeBackend(Backend):
    """Simulated card built from a captured description of a real one."""

    simulated = True

    def __init__(self, path: str = FIXTURE_18I20, extras: bool = False):
        """``extras`` adds the controls of the patched driver (meters, front
        panel, save, monitor masks), with just enough behaviour to test against."""
        with open(path) as f:
            data = json.load(f)
        self.saves = 0
        self.connection = 1
        # Set to make the app treat this simulated unit like a real one that
        # can restart (it normally skips that for the demo).
        self.honour_trust = False
        self.name = data["name"] + " (simulated)"
        self.usb_id = data["usb_id"]
        self._elems = [ElemInfo(**e) for e in data["elements"]]
        self._values = {int(k): list(v) for k, v in data["values"].items()}
        self.writes: list[tuple[str, list[int]]] = []
        self.on_write: Callable[[ElemInfo, list[int]], None] | None = None
        # What the simulated unit is really doing, which after a power cycle
        # differs from what it reports (see power_cycle), and test signals
        # playing on DAW channels ({"PCM 7": linear amplitude}) for the meters.
        self.true_gains: dict[int, int] = {}
        self.playing: dict[str, float] = {}
        if extras:
            numid = max(e.numid for e in self._elems)
            for name, kind, count, hi, writable, start in (
                ("Input Level Meter", TYPE_INTEGER, 18, 65535, False, 0),
                ("Mix Level Meter", TYPE_INTEGER, 8, 65535, False, 0),
                ("PCM Level Meter", TYPE_INTEGER, 20, 65535, False, 0),
                ("Monitor Hardware Status", TYPE_INTEGER, 4, 255, False, 0),
                ("Save To HW", TYPE_BOOLEAN, 1, 0, True, 0),
                ("Monitor Hardware Mute", TYPE_BOOLEAN, 1, 0, True, 0),
                ("Monitor Hardware Dim", TYPE_BOOLEAN, 1, 0, True, 0),
                ("Monitor Mask 0x0a", TYPE_INTEGER, 1, 1023, True, 0),
                ("Monitor Mask 0x0b", TYPE_INTEGER, 1, 1023, True, 0),
                ("Monitor Mask 0x0c", TYPE_INTEGER, 1, 1023, True, 0),
                ("Monitor Unit 20 Control 8", TYPE_BOOLEAN, 1, 0, True, 0),
                *((f"Monitor Unit 20 Control 10 Pair {i}", TYPE_BOOLEAN, 1, 0, True, 0)
                  for i in range(1, 6)),
            ):
                numid += 1
                self._elems.append(ElemInfo(numid, name, 0, kind, count, True, writable, 0, hi))
                self._values[numid] = [start] * count
            self.by_name = {e.name: e for e in self._elems}
            self._values[self.by_name["Monitor Hardware Status"].numid] = [30, 125, 0, 0]
            # A unit nobody has configured: knob, Mute and Dim act on outputs 1/2.
            self._mask_ids = [self.by_name[f"Monitor Mask 0x0{c}"].numid for c in "abc"]
            for numid in self._mask_ids:
                self._values[numid] = [3]
            self._saved_masks = [3, 3, 3]

    def elements(self):
        return list(self._elems)

    def read(self, e):
        if e.name == "PCM Level Meter" and self.playing:
            return [min(65535, round(self.playing.get(f"PCM {i + 1}", 0.0) * 65535))
                    for i in range(e.count)]
        if e.name == "Mix Level Meter" and self.playing:
            return self._mix_meters(e.count)
        return list(self._values[e.numid])

    def _mix_meters(self, count: int) -> list[int]:
        """Peak of each mix bus from the test signals, the routing and the
        gains the unit is really using."""
        routes = {e.name.split(" Input")[0]: e for e in self._elems if e.name.endswith("Input Playback Route")}
        out = []
        for bus in range(count):
            total = 0.0
            for prefix, route in routes.items():
                source = route.items[self._values[route.numid][0]]
                amp = self.playing.get(source, 0.0)
                cell = next((c for c in self._elems
                             if c.name == f"{prefix} Mix {chr(ord('A') + bus)} Playback Volume"), None)
                if amp and cell:
                    level = self.true_gains.get(cell.numid, self._values[cell.numid][0])
                    total += amp * 10 ** ((level - 128) / 20) if level else 0.0
            out.append(min(65535, round(total * 65535)))
        return out

    def write(self, e, values):
        if not e.writable:
            raise alsa.AlsaError(1, f"write {e.name}: Operation not permitted")
        vals = []
        for v in values[: e.count]:
            if e.type == TYPE_BOOLEAN:
                v = int(bool(v))
            elif e.type == TYPE_ENUMERATED:
                v = max(0, min(len(e.items) - 1, int(v)))
            elif e.type == TYPE_INTEGER:
                v = max(e.min, min(e.max, int(v)))
            vals.append(v)
        self._values[e.numid] = vals
        self.true_gains.pop(e.numid, None)          # what is sent is what it uses
        self.writes.append((e.name, vals))
        if e.name == "Save To HW":                       # a trigger: reads back as off
            self.saves += vals[0]
            self._values[e.numid] = [0]
            self._saved_masks = [self._values[i][0] for i in self._mask_ids]
        elif e.name in ("Monitor Hardware Mute", "Monitor Hardware Dim"):
            status = self._values[self.by_name["Monitor Hardware Status"].numid]
            status[2 if e.name.endswith("Mute") else 3] = vals[0]
        if self.on_write:
            self.on_write(e, vals)

    def sample_rate(self):
        return 48000

    def connection_id(self):
        return f"sim/{self.connection}"

    def power_cycle(self, misreport: bool = False, misreport_mutes: bool = True) -> None:
        """The unit restarts: new connection, masks as last saved. With
        ``misreport`` the driver then reads levels the way a real one does
        after power-on: every gain and level 0 dB, every switch muted."""
        self.connection += 1
        for numid, value in zip(getattr(self, "_mask_ids", []), getattr(self, "_saved_masks", [])):
            self._values[numid] = [value]
        if misreport:
            for e in self._elems:
                if e.writable and e.name.endswith("Playback Volume"):
                    if e.count == 1:          # what it really uses survives; reports do not
                        self.true_gains.setdefault(e.numid, self._values[e.numid][0])
                    self._values[e.numid] = [128] * e.count
                elif misreport_mutes and e.writable and e.name.endswith("Playback Switch"):
                    # the stock driver's two-byte mute read (fixed in the patch)
                    self._values[e.numid] = [0] * e.count


def find_cards() -> list[tuple[int, str, str]]:
    """Sound cards that are supported first-generation Scarletts."""
    return [(n, usbid, name) for n, usbid, name in alsa.list_cards() if usbid in SUPPORTED_USB_IDS]


def capture_fixture(card: int) -> dict:
    """Describe a real card (read-only) in the format FakeBackend loads."""
    b = AlsaBackend(card)
    try:
        elems = b.elements()
        values = {}
        for e in elems:
            if e.readable and e.type in (TYPE_BOOLEAN, TYPE_INTEGER, TYPE_ENUMERATED):
                values[str(e.numid)] = b.read(e)
        return {
            "name": b.name,
            "usb_id": b.usb_id,
            "elements": [vars(e) for e in elems
                         if e.type in (TYPE_BOOLEAN, TYPE_INTEGER, TYPE_ENUMERATED)],
            "values": values,
        }
    finally:
        b.close()
