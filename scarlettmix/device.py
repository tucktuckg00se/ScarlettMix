"""Model of a first-generation Scarlett as the kernel driver presents it.

The structure is discovered from the control names rather than hard-coded,
so every model handled by the kernel's mixer_scarlett driver gets a sensible
layout. Values are cached and kept current from ALSA change events.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable

from .alsa import ElemInfo
from . import names
from .backend import SUPPORTED_USB_IDS, Backend

# The kernel exposes gains as 0..134 in 1 dB steps, where 0 is -128 dB.
LEVEL_BIAS = 128
METER_FLOOR_DB = -120.0


def level_to_db(value: int) -> int:
    return value - LEVEL_BIAS


def db_to_level(db: float, e: ElemInfo | None = None) -> int:
    lo, hi = (e.min, e.max) if e else (0, 134)
    return max(lo, min(hi, round(db) + LEVEL_BIAS))


@dataclass(eq=False)   # compared and hashed by identity
class Output:
    """A stereo output pair ("Master N (label)" in the driver)."""

    number: int
    label: str
    volume: ElemInfo | None = None
    switch: ElemInfo | None = None
    source_l: ElemInfo | None = None
    source_r: ElemInfo | None = None

    @property
    def sources(self) -> list[ElemInfo]:
        return [s for s in (self.source_l, self.source_r) if s]


_RE_MATRIX_IN = re.compile(r"^Matrix (\d+) Input Playback Route$")
_RE_MATRIX_MIX = re.compile(r"^Matrix (\d+) Mix ([A-Z]) Playback Volume$")
_RE_OUT = re.compile(r"^Master (\d+) \((.+)\) Playback (Volume|Switch)$")
_RE_OUT_SRC = re.compile(r"^Master (\d+)([LR]) \((.+)\) Source Playback Enum$")
_RE_CAPTURE = re.compile(r"^Input Source (\d+) Capture Route$")
_RE_INPUT_SW = re.compile(r"^Input (\d+) (.+) Switch$")


class Device:
    def __init__(self, backend: Backend):
        self.backend = backend
        self.model_name = SUPPORTED_USB_IDS.get(backend.usb_id, backend.name)
        self.elements = backend.elements()
        self.by_name = {e.name: e for e in self.elements if e.index == 0}
        self.by_numid = {e.numid: e for e in self.elements}
        self._cache: dict[int, list[int]] = {}
        self._listeners: list[Callable[[ElemInfo], None]] = []
        # True while the driver's readings of levels and switches cannot be
        # believed (the unit has powered on since everything was last sent).
        # Changes are then always forced through, and re-reads are skipped.
        self.untrusted = False

        matrix_in: dict[int, ElemInfo] = {}
        self.matrix_gain: dict[tuple[int, int], ElemInfo] = {}
        outputs: dict[int, Output] = {}
        capture: dict[int, ElemInfo] = {}
        self.input_switches: list[ElemInfo] = []
        mix_letters: set[str] = set()

        for e in self.elements:
            if m := _RE_MATRIX_IN.match(e.name):
                matrix_in[int(m.group(1)) - 1] = e
            elif m := _RE_MATRIX_MIX.match(e.name):
                mix_letters.add(m.group(2))
                self.matrix_gain[(int(m.group(1)) - 1, ord(m.group(2)) - ord("A"))] = e
            elif m := _RE_OUT.match(e.name):
                out = outputs.setdefault(int(m.group(1)), Output(int(m.group(1)), m.group(2)))
                setattr(out, "volume" if m.group(3) == "Volume" else "switch", e)
            elif m := _RE_OUT_SRC.match(e.name):
                out = outputs.setdefault(int(m.group(1)), Output(int(m.group(1)), m.group(3)))
                setattr(out, "source_l" if m.group(2) == "L" else "source_r", e)
            elif m := _RE_CAPTURE.match(e.name):
                capture[int(m.group(1)) - 1] = e
            elif _RE_INPUT_SW.match(e.name):
                self.input_switches.append(e)

        self.matrix_inputs = [matrix_in[i] for i in sorted(matrix_in)]
        self.mix_names = sorted(mix_letters)
        self.outputs = [outputs[i] for i in sorted(outputs)]
        self.capture = [capture[i] for i in sorted(capture)]
        self.master_volume = self.by_name.get("Master Playback Volume")
        self.master_switch = self.by_name.get("Master Playback Switch")
        self.clock_source = self.by_name.get("Sample Clock Source")
        self.sync_status = self.by_name.get("Sample Clock Sync Status")
        # Controls only a patched driver offers (see docs/missing-features.md).
        n = self.by_name.get
        self.meter_inputs = n("Input Level Meter")
        self.meter_mixes = n("Mix Level Meter")
        self.meter_pcm = n("PCM Level Meter")
        self.hw_status = n("Monitor Hardware Status")
        self.hw_save = n("Save To HW")
        self.hw_mute = n("Monitor Hardware Mute")
        self.hw_dim = n("Monitor Hardware Dim")
        self.hw_masks = [e for e in (n("Monitor Mask 0x0a"), n("Monitor Mask 0x0b"),
                                     n("Monitor Mask 0x0c")) if e]
        # Pad on the main monitor outputs (test-driver name until it has a final one).
        self.hw_pad = n("Monitor Unit 20 Control 8")
        # Mono per analogue output pair (test-driver name until it has a final one).
        self.hw_mono = [e for e in (n(f"Monitor Unit 20 Control 10 Pair {i}") for i in range(1, 9)) if e]
        self.input_switches.sort(key=lambda e: [int(t) if t.isdigit() else t
                                                for t in re.split(r"(\d+)", e.name)])

    @property
    def n_inputs(self) -> int:
        return len(self.matrix_inputs)

    @property
    def n_mixes(self) -> int:
        return len(self.mix_names)

    # -- meters, front panel and save (patched driver only) ----------------

    @property
    def has_meters(self) -> bool:
        return bool(self.meter_inputs and self.meter_mixes and self.meter_pcm)

    @property
    def has_monitor(self) -> bool:
        return bool(self.hw_status and self.hw_mute and self.hw_dim and len(self.hw_masks) == 3)

    @staticmethod
    def meter_db(raw: int) -> float:
        """Meter value (linear peak, 65535 = full scale) in dBFS."""
        return 20.0 * math.log10(raw / 65535.0) if raw > 0 else METER_FLOOR_DB

    def read_meters(self) -> dict[str, list[float]]:
        """Peak levels since the last read, in dBFS, per hardware input, mix and DAW channel."""
        return {key: [self.meter_db(v) for v in self.backend.read(e)]
                for key, e in (("inputs", self.meter_inputs), ("mixes", self.meter_mixes),
                               ("pcm", self.meter_pcm))}

    def read_status(self) -> tuple[int, bool, bool]:
        """Front panel: (monitor knob attenuation in dB, Mute pressed, Dim pressed)."""
        v = self.backend.read(self.hw_status)
        return v[0], bool(v[2]), bool(v[3])

    def save_to_hardware(self) -> None:
        """Make the interface keep its current routing and mixer across power cycles."""
        self.backend.write(self.hw_save, [1])

    def write(self, e: ElemInfo, values: list[int]) -> None:
        """Send a control regardless of what was last read (for write-only registers)."""
        self.backend.write(e, [int(v) for v in values])
        self._cache[e.numid] = [int(v) for v in values]
        self._notify(e)

    def analogue_channels(self) -> list[tuple[Output, int]]:
        """Analogue outputs 1..n in order, as (output pair, 0 for left / 1 for right)."""
        return [(o, side) for o in self.outputs
                if "SPDIF" not in o.label and "ADAT" not in o.label for side in (0, 1)]

    def hardware_inputs(self) -> list[str]:
        """Source names of the hardware inputs, in input-meter order."""
        if not self.capture:
            return []
        return [it for it in self.capture[0].items
                if it != "Off" and not it.startswith(("PCM ", "Mix "))]

    def source_meter(self, item: str) -> tuple[str, int] | None:
        """Which meter shows a given source, as (bank, index), if any."""
        if item.startswith("PCM "):
            return "pcm", int(item[4:]) - 1
        inputs = self.hardware_inputs()
        return ("inputs", inputs.index(item)) if item in inputs else None

    def output_sources(self) -> list[tuple[str, ElemInfo]]:
        """Every physical output channel with its source selector, in order."""
        out = []
        for o in self.outputs:
            for label, e in zip(names.output_channels(o.label), (o.source_l, o.source_r)):
                if e:
                    out.append((label, e))
        return out

    def headphones(self, o: Output) -> int | None:
        """Which headphone socket (1, 2, ...) mirrors this output pair, if any."""
        return names.HEADPHONES.get(self.backend.usb_id, {}).get(o.label)

    def mix_sent_to(self, buses: list[int], o: Output) -> bool:
        """Whether an output pair is playing the given mono or stereo mix."""
        want = [f"Mix {self.mix_names[b]}" for b in (buses if len(buses) == 2 else buses * 2)]
        return bool(o.sources) and [self.item(e) for e in (o.source_l, o.source_r) if e] == want

    def send_mix(self, buses: list[int], o: Output, on: bool) -> None:
        """Route a mix to an output pair (a mono mix feeds both sides), or stop."""
        want = [f"Mix {self.mix_names[b]}" for b in (buses if len(buses) == 2 else buses * 2)]
        for e, item in zip((o.source_l, o.source_r), want):
            if not e:
                continue
            if on:
                self.set_item(e, item)
            elif self.item(e) == item:
                self.set_item(e, "Off")

    # -- values ------------------------------------------------------------

    def get(self, e: ElemInfo) -> list[int]:
        if e.numid not in self._cache:
            self._cache[e.numid] = self.backend.read(e)
        return list(self._cache[e.numid])

    def set(self, e: ElemInfo, values: list[int], force: bool = False) -> bool:
        """Write a control if it differs from the current value.

        ``force`` makes sure a level or switch really reaches the interface.
        After a power cycle the driver reports levels the unit does not have
        (silent mixer gains read as 0 dB), and it drops writes that match what
        it believes. Writing a different value first gets the real one through.
        """
        values = [int(v) for v in values]
        force = force or self.untrusted
        if force and e.writable and not e.items:
            if e.max > e.min:
                other = [v - 1 if v > e.min else v + 1 for v in values]
            else:
                other = [0 if v else 1 for v in values]
            self.backend.write(e, other)
        elif self.get(e) == values:
            return False
        self.backend.write(e, values)
        # Read back: the driver may clamp, and paired controls can interact.
        self._cache[e.numid] = self.backend.read(e)
        self._notify(e)
        return True

    def believe(self, e: ElemInfo, values: list[int]) -> None:
        """Show this as the control's value without reading or writing it."""
        self._cache[e.numid] = [int(v) for v in values]

    def item(self, e: ElemInfo) -> str:
        v = self.get(e)[0]
        return e.items[v] if 0 <= v < len(e.items) else ""

    def set_item(self, e: ElemInfo, name: str) -> bool:
        if name not in e.items:
            return False
        return self.set(e, [e.items.index(name)])

    def matrix_levels(self) -> dict[tuple[int, int], int]:
        return {key: self.get(e)[0] for key, e in self.matrix_gain.items()}

    # -- change tracking ---------------------------------------------------

    def add_listener(self, fn: Callable[[ElemInfo], None]) -> None:
        self._listeners.append(fn)

    def _notify(self, e: ElemInfo) -> None:
        for fn in list(self._listeners):
            fn(e)

    def poll_events(self) -> bool:
        """Pick up changes made by other programs. Returns False if the card is gone."""
        changed, gone = self.backend.drain_events()
        if gone:
            return False
        for numid in changed:
            e = self.by_numid.get(numid)
            if not e or not e.readable:
                continue
            new = self.backend.read(e)
            if self._cache.get(numid) != new:
                self._cache[numid] = new
                self._notify(e)
        return True

    def refresh(self, e: ElemInfo) -> None:
        """Re-read a control that changes without an event (e.g. sync status)."""
        if self.untrusted and e.writable and not e.items:
            return      # the driver's reading would replace what we believe with nonsense
        new = self.backend.read(e)
        if self._cache.get(e.numid) != new:
            self._cache[e.numid] = new
            self._notify(e)
