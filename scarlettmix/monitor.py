"""The monitor section's output buttons, as in the original MixControl.

Each analogue output is in one of three states:

  blue    follows the monitor section: the Mute and Dim buttons act on it and
          its level tracks the monitor knob
  silver  independent: left alone, at whatever level its own fader is set to
  red     muted

How the interface does this (measured on an 18i20, see
docs/missing-features.md):

  * Two bit masks name the outputs its Mute and Dim buttons act on.
  * A third mask names outputs whose level is NOT under software control:
    while an output is in it, level changes sent from the computer are held
    back, and they take effect when the output leaves the mask. The first
    output pair is normally in it, and that pair is what the knob moves.
  * The knob moves no other output by itself, so for other blue outputs it
    is followed in software: read its position, set their level. Those
    outputs must therefore stay out of the third mask.

The masks cannot be read back, so the button states are kept in a file.
Needs the controls of the patched driver.
"""

from __future__ import annotations

import json
import os

from .device import Device, db_to_level

BLUE, SILVER, RED = "blue", "silver", "red"
STATES = (BLUE, SILVER, RED)

# The original's "Monitor Preset..." menu: which analogue outputs (numbered
# from 1) follow the monitor section. Read from the 18i20 table in
# Scarlett MixControl 1.10.
PRESETS = {
    "1235:800c": [
        ("Mono", [3]),
        ("Stereo", [1, 2]),
        ("Quad", [1, 2, 7, 8]),
        ("2.1 Surround", [1, 2, 4]),
        ("5.1 Surround", [1, 2, 3, 4, 7, 8]),
        ("7.1 Surround", [1, 2, 3, 4, 5, 6, 7, 8]),
        ("Mid+Phones 1", [7, 8]),
        ("Mini+Phones 2", [9, 10]),
    ],
}


class Monitor:
    def __init__(self, device: Device):
        self.device = device
        self.channels = device.analogue_channels()
        # How the unit behaves when nothing has configured it: first pair only.
        self.states = [BLUE if i < 2 else SILVER for i in range(len(self.channels))]
        # Level each followed output had before the knob took it over, so it
        # can be given back when the output stops following.
        self.parked: dict[int, int] = {}
        # First-pair outputs already handed over to software level control.
        self.released: set[int] = set()
        # Mono: blue pairs play the sum of their two channels on both.
        self.mono = False
        self._mtime = 0.0

    @property
    def available(self) -> bool:
        return self.device.has_monitor and bool(self.channels)

    def presets(self) -> list[tuple[str, list[int]]]:
        return [(name, outs) for name, outs in PRESETS.get(self.device.backend.usb_id, [])
                if max(outs) <= len(self.channels)]

    def use_preset(self, outputs: list[int]) -> None:
        """Listed outputs follow the monitor section; the rest become independent."""
        self.states = [BLUE if i + 1 in outputs else SILVER for i in range(len(self.channels))]

    # -- persistence ---------------------------------------------------------

    def path(self) -> str:
        from .presets import config_dir
        return os.path.join(config_dir(),
                            f"monitor-{self.device.backend.usb_id.replace(':', '-')}.json")

    def set_states(self, states) -> bool:
        """Adopt a list of states if it fits this device."""
        states = list(states or [])
        if len(states) != len(self.channels) or any(s not in STATES for s in states):
            return False
        self.states = states
        return True

    def load(self) -> bool:
        try:
            self._mtime = os.path.getmtime(self.path())
            with open(self.path()) as f:
                data = json.load(f)
            if not self.set_states(data.get("outputs")):
                return False
            # Keep levels this process parked itself if the file does not know them.
            self.parked = {**self.parked,
                           **{int(k): int(v) for k, v in data.get("parked", {}).items()}}
            self.released = {int(i) for i in data.get("released", [])}
            self.mono = bool(data.get("mono", False))
            return True
        except (OSError, ValueError, AttributeError):
            return False

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path()), exist_ok=True)
        tmp = self.path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"outputs": self.states, "parked": self.parked,
                       "released": sorted(self.released), "mono": self.mono}, f)
        os.replace(tmp, self.path())
        self._mtime = os.path.getmtime(self.path())

    def reload_if_changed(self) -> bool:
        """Pick up states another program saved. True if they changed."""
        try:
            if os.path.getmtime(self.path()) == self._mtime:
                return False
        except OSError:
            return False
        before = list(self.states)
        return self.load() and self.states != before

    # -- hardware ------------------------------------------------------------

    def mask(self) -> int:
        """Outputs the Mute and Dim buttons act on: every blue one."""
        return sum(1 << i for i, s in enumerate(self.states) if s == BLUE)

    def _knob_owned(self, i: int) -> bool:
        """Whether output i belongs to the pair the interface's knob moves."""
        return self.channels[i][0] is self.channels[0][0]

    def hardware_mask(self) -> int:
        """Outputs whose level is left to the interface: blue ones in the knob's pair."""
        return sum(1 << i for i, s in enumerate(self.states) if s == BLUE and self._knob_owned(i))

    def apply(self) -> None:
        """Send the button states to the interface."""
        self._release_knob_pair()
        self.device.write(self.device.hw_masks[0], [self.hardware_mask()])
        for e in self.device.hw_masks[1:]:
            self.device.write(e, [self.mask()])
        self._park_levels()
        # Mono applies to a pair only when both of its outputs are blue, as in the original.
        for pair, e in enumerate(self.device.hw_mono):
            both = self.states[2 * pair:2 * pair + 2] == [BLUE, BLUE]
            self.device.write(e, [int(self.mono and both)])
        for output in dict.fromkeys(o for o, _ in self.channels):
            if not output.switch:
                continue
            self.device.refresh(output.switch)
            values = self.device.get(output.switch)     # 1 = audible
            for i, (o, side) in enumerate(self.channels):
                if o is output and side < len(values):
                    values[side] = 0 if self.states[i] == RED else 1
            self.device.set(output.switch, values)

    def _release_knob_pair(self) -> None:
        """Before an output of the knob's pair leaves the interface's control,
        set its software level to where the knob is now. That level takes over
        the moment the mask is written, so without this it would jump to
        whatever was last sent, typically close to full."""
        output = self.channels[0][0]
        if not output.volume:
            return
        leaving = [i for i, s in enumerate(self.states)
                   if self._knob_owned(i) and s != BLUE and i not in self.released]
        self.released -= {i for i, s in enumerate(self.states) if s == BLUE}
        if not leaving:
            return
        knob = self.device.read_status()[0]
        self.device.refresh(output.volume)
        values = self.device.get(output.volume)
        for i in leaving:
            side = self.channels[i][1]
            if side < len(values):
                values[side] = db_to_level(-knob, output.volume)
            self.released.add(i)
        # Forced: the driver may believe the level is already this value.
        self.device.set(output.volume, values, force=True)

    def _followed(self, i: int) -> bool:
        """Whether output i's level is driven by the knob from software."""
        return self.states[i] == BLUE and not self._knob_owned(i)

    def _park_levels(self) -> None:
        """Remember the level of outputs that start following, and give it
        back to outputs that stop."""
        for output in dict.fromkeys(o for o, _ in self.channels):
            if not output.volume:
                continue
            self.device.refresh(output.volume)          # another program may have moved it
            values = self.device.get(output.volume)
            for i, (o, side) in enumerate(self.channels):
                if o is not output or side >= len(values):
                    continue
                if self._followed(i):
                    self.parked.setdefault(i, values[side])
                elif i in self.parked:
                    values[side] = self.parked.pop(i)
            self.device.set(output.volume, values)

    def follow(self, attenuation: int) -> int:
        """Make blue outputs track the monitor knob. The first pair is left to
        the interface, which already applies the knob there. Returns writes made."""
        written = 0
        first = self.channels[0][0] if self.channels else None
        for output in dict.fromkeys(o for o, _ in self.channels):
            if output is first or not output.volume:
                continue
            if not any(self._followed(i) for i, (o, _) in enumerate(self.channels) if o is output):
                continue
            self.device.refresh(output.volume)
            values = self.device.get(output.volume)
            for i, (o, side) in enumerate(self.channels):
                if o is output and side < len(values) and self._followed(i):
                    values[side] = db_to_level(-attenuation, output.volume)
            written += self.device.set(output.volume, values)
        return written
