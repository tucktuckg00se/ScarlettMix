"""The monitor section's output buttons, as in the original MixControl.

Each analogue output is in one of three states:

  blue    follows the monitor section: the Mute and Dim buttons act on it and
          its level tracks the monitor knob
  silver  independent: left alone, at whatever level its own fader is set to
  red     muted

How the interface does this (measured on an 18i20, see
docs/missing-features.md):

  * Two bit masks name the outputs its Mute and Dim buttons act on. They
    take effect at once.
  * A third mask names the outputs its monitor knob controls. The interface
    reads it at power-on only: adding an output needs Save To HW and a
    restart of the unit. Removing one takes effect at once. While an output
    is in the mask, level changes from the computer are held back.

So this module never adds an output to the knob mask of a running unit, only
removes. The mask in the unit is then always exactly the set of outputs its
knob is really controlling, and can simply be read. An output made blue since
the last restart is "pending": it is written into the saved copy by
save_to_hardware(), and until the unit restarts the knob is followed for it
in software, by reading the knob's position and setting the output's level.

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
        # How a unit behaves when nothing has configured it: first pair only.
        self.states = [BLUE if i < 2 else SILVER for i in range(len(self.channels))]
        # Level each software-followed output had before the knob took it
        # over, so it can be given back when the output stops following.
        self.parked: dict[int, int] = {}
        # Mono: blue pairs play the sum of their two channels on both.
        self.mono = False
        # Pad: the interface's own attenuator on the main monitor outputs.
        self.pad = False
        # Button states as of the last Save to Hardware from here, if known.
        self.saved_states: list[str] | None = None
        self._live: int | None = None
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

    def _fits(self, states) -> bool:
        return (isinstance(states, list) and len(states) == len(self.channels)
                and all(s in STATES for s in states))

    def set_states(self, states) -> bool:
        """Adopt a list of states if it fits this device."""
        if not self._fits(states):
            return False
        self.states = list(states)
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
            self.mono = bool(data.get("mono", False))
            self.pad = bool(data.get("pad", False))
            saved = data.get("saved")
            self.saved_states = list(saved) if self._fits(saved) else None
            return True
        except (OSError, ValueError, AttributeError):
            return False

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path()), exist_ok=True)
        tmp = self.path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"outputs": self.states, "parked": self.parked, "mono": self.mono,
                       "pad": self.pad, "saved": self.saved_states}, f)
        os.replace(tmp, self.path())
        self._mtime = os.path.getmtime(self.path())

    def reload_if_changed(self) -> bool:
        """Pick up states another program saved. True if they changed."""
        try:
            if os.path.getmtime(self.path()) == self._mtime:
                return False
        except OSError:
            return False
        before = (list(self.states), self.mono, self.pad)
        return self.load() and (self.states, self.mono, self.pad) != before

    # -- what the unit is doing ------------------------------------------------

    def refresh(self) -> int:
        """Read which outputs the unit's knob is controlling right now."""
        self._live = self.device.backend.read(self.device.hw_masks[0])[0]
        return self._live

    def adopt_unit(self) -> bool:
        """Take the button states from the interface itself. It is the truth
        about what follows the monitor section: after a restart it holds what
        was last saved, whatever was clicked here since. Only "muted" (red)
        still comes from our own note, since the unit does not report output
        mutes reliably. Reads only. Returns True if anything changed."""
        knob, mute, dim = (self.device.backend.read(e)[0] for e in self.device.hw_masks)
        self._live = knob
        blue = knob | mute | dim
        before = (list(self.states), self.mono, self.pad)
        for i in range(len(self.states)):
            if blue >> i & 1:
                self.states[i] = BLUE
            elif self.states[i] == BLUE:
                self.states[i] = SILVER
        if self.device.hw_pad:
            self.pad = bool(self.device.backend.read(self.device.hw_pad)[0])
        if self.device.hw_mono:
            self.mono = any(self.device.backend.read(e)[0] for e in self.device.hw_mono)
        return (self.states, self.mono, self.pad) != before

    def live_mask(self) -> int:
        return self.refresh() if self._live is None else self._live

    def mask(self) -> int:
        """Every blue output: what the Mute and Dim buttons act on."""
        return sum(1 << i for i, s in enumerate(self.states) if s == BLUE)

    def on_knob(self, i: int) -> bool:
        """Whether the unit itself is moving output i with the knob."""
        return self.states[i] == BLUE and bool(self.live_mask() >> i & 1)

    def pending(self) -> list[int]:
        """Blue outputs the unit will only take over after a save and a restart."""
        return [i for i, s in enumerate(self.states) if s == BLUE and not self.on_knob(i)]

    def unsaved(self) -> bool:
        """Whether the buttons differ from what was last saved to the unit from here."""
        return self.saved_states is not None and self.saved_states != self.states

    # -- hardware ------------------------------------------------------------

    def apply(self) -> None:
        """Send the button states to the interface."""
        live = self.refresh()
        keep = live & self.mask()
        if keep != live:
            self._release(live & ~keep)
            self.device.write(self.device.hw_masks[0], [keep])
            self._live = keep
        for e in self.device.hw_masks[1:]:
            self.device.write(e, [self.mask()])
        if self.device.hw_pad:
            self.device.write(self.device.hw_pad, [int(self.pad)])
        # Mono applies to a pair only when both of its outputs are blue, as in the original.
        for pair, e in enumerate(self.device.hw_mono):
            both = self.states[2 * pair:2 * pair + 2] == [BLUE, BLUE]
            self.device.write(e, [int(self.mono and both)])
        self._park_levels()
        for output in dict.fromkeys(o for o, _ in self.channels):
            if not output.switch:
                continue
            self.device.refresh(output.switch)
            values = self.device.get(output.switch)     # 1 = audible
            for i, (o, side) in enumerate(self.channels):
                if o is output and side < len(values):
                    values[side] = 0 if self.states[i] == RED else 1
            self.device.set(output.switch, values)

    def _release(self, bits: int) -> None:
        """Before outputs leave the unit's knob control, send them the level
        that takes over the moment the mask is written: the level they had
        before they started following, if we know it; otherwise where the
        knob is now, so that nothing jumps (for main monitors the last level
        sent is typically close to full)."""
        knob = self.device.read_status()[0]
        for output in dict.fromkeys(o for o, _ in self.channels):
            if not output.volume:
                continue
            leaving = [(i, side) for i, (o, side) in enumerate(self.channels)
                       if o is output and bits >> i & 1]
            if not leaving:
                continue
            self.device.refresh(output.volume)
            values = self.device.get(output.volume)
            for i, side in leaving:
                own = self.parked.pop(i, None)
                if side < len(values):
                    values[side] = db_to_level(-knob, output.volume) if own is None else own
            # Forced: the driver may believe the level is already this value.
            self.device.set(output.volume, values, force=True)

    def _followed(self, i: int) -> bool:
        """Whether output i's level is driven by the knob from software."""
        return self.states[i] == BLUE and not self.on_knob(i)

    def _park_levels(self) -> None:
        """Remember the level of outputs that start following in software,
        and give it back to outputs that stop."""
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
                elif i in self.parked and self.states[i] != BLUE:
                    values[side] = self.parked.pop(i)
            self.device.set(output.volume, values)

    def own_levels(self) -> dict[str, list[int]]:
        """Each analogue output's own level: for outputs this program is
        moving with the knob, the level they had before, not where the knob
        has put them."""
        levels = {}
        for output in dict.fromkeys(o for o, _ in self.channels):
            if not output.volume:
                continue
            values = self.device.get(output.volume)
            for i, (o, side) in enumerate(self.channels):
                if o is output and side < len(values) and i in self.parked:
                    values[side] = self.parked[i]
            levels[output.volume.name] = values
        return levels

    def stop_following(self) -> None:
        """Called when this program stops: outputs it was moving with the
        knob go back to their own level instead of staying wherever the knob
        happened to be, where nothing could raise them again."""
        if not any(self._followed(i) for i in range(len(self.states))):
            return
        for name, values in self.own_levels().items():
            self.device.set(self.device.by_name[name], values)
        self.parked = {i: v for i, v in self.parked.items() if not self._followed(i)}
        self.save()

    def follow(self, attenuation: int) -> int:
        """Make pending blue outputs track the monitor knob. Outputs the unit
        already moves are left alone. Returns the number of writes made."""
        written = 0
        noted = False
        for output in dict.fromkeys(o for o, _ in self.channels):
            if not output.volume:
                continue
            if not any(self._followed(i) for i, (o, _) in enumerate(self.channels) if o is output):
                continue
            self.device.refresh(output.volume)
            values = self.device.get(output.volume)
            for i, (o, side) in enumerate(self.channels):
                if o is output and side < len(values) and self._followed(i):
                    # Following can start by itself (at startup the buttons
                    # come from the unit): note the output's own level before
                    # the first change, or it could never be given back.
                    if i not in self.parked:
                        self.parked[i] = values[side]
                        noted = True
                    values[side] = db_to_level(-attenuation, output.volume)
            written += self.device.set(output.volume, values)
        if noted:
            self.save()
        return written

    def save_to_hardware(self) -> bool:
        """Store the unit's settings with every blue output in the knob mask.

        The mask is written in full only for the moment of the save and then
        put back, so the running unit is not changed. Returns True if some
        output will only come under the knob once the unit is restarted.
        """
        live = self.refresh()
        want = self.mask()
        if want != live:
            self.device.write(self.device.hw_masks[0], [want])
        try:
            self.device.save_to_hardware()
        finally:
            if want != live:
                self.device.write(self.device.hw_masks[0], [live])
        self.saved_states = list(self.states)
        self.save()
        return bool(self.pending())
