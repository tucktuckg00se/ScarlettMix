"""The monitor section's output buttons, as in the original MixControl.

This module keeps no settings of its own. Everything it reports is read from
the interface, and it changes the interface only when asked to.

The interface holds three lists of analogue outputs (bit masks; measured on
an 18i20, see docs/missing-features.md):

  * the outputs its Mute button silences, and the outputs its Dim button
    lowers. Both take effect at once.
  * the outputs its monitor knob controls. The interface reads this one at
    power-on only: adding an output needs Save To HW and a restart of the
    unit. Removing one takes effect at once.

Each output is shown in one of three states:

  blue    in the monitor group: Mute and Dim act on it. ``on_knob`` tells
          whether the knob controls it too, or only will once the unit has
          been saved and restarted (``pending``)
  silver  independent, at its own level
  red     muted (the output's own mute switch)

So an output is never added to the knob list of a running unit, only removed,
and that list is always exactly what the knob is really controlling.

Needs the controls of the patched driver.
"""

from __future__ import annotations

from .device import Device, db_to_level

BLUE, SILVER, RED = "blue", "silver", "red"
STATES = (BLUE, SILVER, RED)

# The original's "Monitor Preset..." menu: which analogue outputs (numbered
# from 1) are in the monitor group. Read from the 18i20 table in
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
        self.states = [SILVER] * len(self.channels)
        self.knob_list = 0          # outputs the unit's knob controls now
        self.group = 0              # outputs its Mute and Dim act on
        self.mono = False
        self.pad = False
        if self.available:
            self.refresh()

    @property
    def available(self) -> bool:
        return self.device.has_monitor and bool(self.channels)

    def presets(self) -> list[tuple[str, list[int]]]:
        return [(name, outs) for name, outs in PRESETS.get(self.device.backend.usb_id, [])
                if max(outs) <= len(self.channels)]

    def fits(self, states) -> bool:
        return (isinstance(states, list) and len(states) == len(self.channels)
                and all(s in STATES for s in states))

    # -- reading the interface -------------------------------------------------

    def _audible(self, i: int) -> bool:
        """Output i's own mute switch. Unknown counts as audible: after a
        restart the driver reports every switch as muted, and unless a saved
        setup was substituted for those readings they mean nothing."""
        output, side = self.channels[i]
        if not output.switch or (self.device.untrusted and not self.device.primed):
            return True
        values = self.device.get(output.switch)
        return side >= len(values) or bool(values[side])

    def refresh(self) -> bool:
        """Read the monitor section from the interface. Reads only. Returns
        True if anything shown has changed."""
        before = (list(self.states), self.knob_list, self.mono, self.pad)
        knob, mute, dim = (self.device.backend.read(e)[0] for e in self.device.hw_masks)
        self.knob_list = knob
        self.group = knob | mute | dim
        self.states = [RED if not self._audible(i) else BLUE if self.group >> i & 1 else SILVER
                       for i in range(len(self.channels))]
        if self.device.hw_pad:
            self.pad = bool(self.device.backend.read(self.device.hw_pad)[0])
        if self.device.hw_mono:
            self.mono = any(self.device.backend.read(e)[0] for e in self.device.hw_mono)
        return (self.states, self.knob_list, self.mono, self.pad) != before

    def on_knob(self, i: int) -> bool:
        """Whether the unit's knob is controlling output i."""
        return self.states[i] == BLUE and bool(self.knob_list >> i & 1)

    def pending(self) -> list[int]:
        """Blue outputs the knob will only take over after a save and a restart."""
        return [i for i, s in enumerate(self.states) if s == BLUE and not self.on_knob(i)]

    # -- changing the interface ------------------------------------------------

    def set_state(self, index: int, state: str) -> None:
        """Change one output. Only that output's place in the lists and its
        own mute are touched; what is shown for the others is never used, so
        a wrong reading elsewhere cannot spread."""
        self.refresh()
        if state == RED:
            group = self.group                           # muting leaves the group alone
        elif state == BLUE:
            group = self.group | 1 << index
        else:
            group = self.group & ~(1 << index)
        self._write_group(group)
        output, side = self.channels[index]
        if output.switch:
            values = self.device.get(output.switch)      # 1 = audible
            if side < len(values) and values[side] != (state != RED):
                values[side] = int(state != RED)
                self.device.set(output.switch, values)
        self._write_mono(self.mono, group)
        self.refresh()

    def _write_group(self, group: int) -> None:
        d = self.device
        keep = self.knob_list & group
        if keep != self.knob_list:
            self._release(self.knob_list & ~keep)
            d.write(d.hw_masks[0], [keep])
        for e in d.hw_masks[1:]:
            if d.backend.read(e)[0] != group:
                d.write(e, [group])

    def use_preset(self, outputs: list[int]) -> None:
        """Listed outputs form the monitor group; the rest become independent."""
        self.apply([BLUE if i + 1 in outputs else SILVER for i in range(len(self.channels))])

    def apply(self, states: list[str]) -> None:
        """Make the interface match a complete set of button states (a preset
        or a monitor preset). Only what differs from what it has now is sent."""
        self.refresh()
        d = self.device
        group = sum(1 << i for i, s in enumerate(states) if s == BLUE)
        self._write_group(group)
        for output in dict.fromkeys(o for o, _ in self.channels):
            if not output.switch:
                continue
            values = d.get(output.switch)               # 1 = audible
            wanted = list(values)
            for i, (o, side) in enumerate(self.channels):
                if o is output and side < len(wanted) and states[i] != self.states[i]:
                    wanted[side] = 0 if states[i] == RED else 1
            if wanted != values:
                d.set(output.switch, wanted)
        self._write_mono(self.mono, group)
        self.refresh()

    def set_mono(self, on: bool) -> None:
        self.refresh()
        self._write_mono(on, self.group)
        self.refresh()

    def _write_mono(self, on: bool, group: int) -> None:
        # Mono applies to a pair only when both of its outputs are in the
        # group, as in the original.
        for pair, e in enumerate(self.device.hw_mono):
            want = int(on and group >> (2 * pair) & 3 == 3)
            if self.device.backend.read(e)[0] != want:
                self.device.write(e, [want])

    def set_pad(self, on: bool) -> None:
        if self.device.hw_pad:
            self.device.write(self.device.hw_pad, [int(on)])
        self.refresh()

    def _release(self, bits: int) -> None:
        """Before outputs leave the unit's knob control, set their level to
        where the knob is now. That level takes over the moment the list is
        written; without this it would jump to whatever was last sent, which
        for the main monitors is typically close to full."""
        knob = self.device.read_status()[0]
        for output in dict.fromkeys(o for o, _ in self.channels):
            if not output.volume:
                continue
            leaving = [side for i, (o, side) in enumerate(self.channels)
                       if o is output and bits >> i & 1]
            if not leaving:
                continue
            values = self.device.get(output.volume)
            for side in leaving:
                if side < len(values):
                    values[side] = db_to_level(-knob, output.volume)
            # Forced: the driver may believe the level is already this value.
            self.device.set(output.volume, values, force=True)

    def save_to_hardware(self) -> bool:
        """Store the unit's settings with the whole monitor group in the knob
        list.

        The list is written in full only for the moment of the save and then
        put back, so the running unit is not changed. Returns True if some
        output will only come under the knob once the unit is restarted.
        """
        self.refresh()
        live, want = self.knob_list, self.group
        if want != live:
            self.device.write(self.device.hw_masks[0], [want])
        try:
            self.device.save_to_hardware()
        finally:
            if want != live:
                self.device.write(self.device.hw_masks[0], [live])
        self.refresh()
        return bool(self.pending())
