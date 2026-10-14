"""What differs from the last Save to Hardware.

One place answers, for every control on screen, "is this what the interface
has stored, or a change that is lost when it is switched off". The record
compared against is the one ``presets.save_saved`` wrote at the last save.
Without a record nothing can be told apart, and nothing is reported as
changed.
"""

from __future__ import annotations

from . import presets
from .alsa import TYPE_ENUMERATED, ElemInfo
from .device import Device
from .mixer import Mix, MixController, MixModel


class Saved:
    def __init__(self, device: Device, record: dict | None):
        self.device = device
        self.record = record
        self.known = bool(record)
        self._controls: dict[str, list] = (record or {}).get("controls", {})
        self.model: MixModel | None = None
        self._gains: dict[tuple[int, int], int] = {}
        if record and "mixer" in record:
            try:
                self.model = MixModel.from_dict(record["mixer"], device.n_inputs, device.n_mixes)
                self._gains = self.model.target_levels()
            except (ValueError, KeyError, TypeError):
                self.model = None
        elif record:
            by_name = {e.name: key for key, e in device.matrix_gain.items()}
            self._gains = {by_name[n]: v for n, v in record.get("matrix", {}).items()
                           if n in by_name}
        # Own levels of outputs the program is moving with the knob (set by
        # whoever owns the monitor): those are compared, not the knob's position.
        self.own_levels: dict[str, list[int]] = {}
        self._monitor: list[str] | None = (record or {}).get("monitor")
        self._monitor_saved: dict | None = (record or {}).get("monitor_saved")

    # -- single controls -------------------------------------------------------

    def control(self, e: ElemInfo | None) -> bool:
        """Whether this control's value differs from the saved one."""
        if e is None or e.name not in self._controls:
            return False
        values = self.own_levels.get(e.name) or self.device.get(e)
        now = [e.items[v] for v in values] if e.type == TYPE_ENUMERATED else values
        return now != self._controls[e.name]

    def cell(self, input_index: int, bus: int) -> bool:
        key = (input_index, bus)
        return key in self._gains and self.device.get(self.device.matrix_gain[key])[0] != self._gains[key]

    # -- the mixer view --------------------------------------------------------

    def _saved_mix(self, mix: Mix) -> Mix | None:
        """The saved mix with the same shape (mono, or the same stereo pair)."""
        if not self.model:
            return None
        return next((m for m in self.model.mixes if m.buses == mix.buses), None)

    def mix(self, mix: Mix) -> bool:
        """Whether anything in this mix differs."""
        return any(self.cell(i, b) for b in mix.buses for i in range(self.device.n_inputs))

    def mix_shape(self, mix: Mix) -> bool:
        return self.known and self.model is not None and self._saved_mix(mix) is None

    def strip(self, mix: Mix, index: int, inputs: list[int]) -> dict[str, bool]:
        """Which of a channel strip's controls differ: fader, pan, mute, solo."""
        was = self._saved_mix(mix)
        if was is None:
            # No saved strip to compare with: go by what reaches the hardware.
            changed = any(self.cell(i, b) for i in inputs for b in mix.buses)
            return dict.fromkeys(("fader", "pan", "mute", "solo"), False) | {"fader": changed}
        a, b = mix.strips[index], was.strips[index]
        return {"fader": a.db != b.db, "pan": abs(a.pan - b.pan) > 1e-6,
                "mute": a.mute != b.mute, "solo": a.solo != b.solo}

    def master(self, mix: Mix) -> dict[str, bool]:
        was = self._saved_mix(mix)
        if was is None:
            return {"fader": False, "mute": False}
        return {"fader": mix.master_db != was.master_db, "mute": mix.master_mute != was.master_mute}

    def link(self, model: MixModel, pair: int) -> bool:
        return self.model is not None and (pair in model.stereo_inputs) != (pair in self.model.stereo_inputs)

    # -- the monitor section ---------------------------------------------------

    def monitor(self, monitor) -> list[bool]:
        if not self._monitor or len(self._monitor) != len(monitor.states):
            return [False] * len(monitor.states)
        return [now != was for now, was in zip(monitor.states, self._monitor)]

    def mono(self, monitor) -> bool:
        return bool(self._monitor_saved) and monitor.mono != self._monitor_saved.get("mono", False)

    def pad(self, monitor) -> bool:
        return bool(self._monitor_saved) and monitor.pad != self._monitor_saved.get("pad", False)

    def waiting_for_restart(self, monitor) -> list[int]:
        """Outputs saved into the knob's list that the running unit's knob
        does not control yet."""
        if not self._monitor_saved:
            return []
        group = self._monitor_saved.get("group", 0)
        return [i for i in monitor.pending() if group >> i & 1]

    # -- everything ------------------------------------------------------------

    def any(self, monitor=None) -> bool:
        if not self.known:
            return False
        if monitor is not None and monitor.available:
            self.own_levels = monitor.own_levels()
        if any(self.control(e) for e in presets._preset_controls(self.device)):
            return True
        if any(self.cell(i, b) for (i, b) in self._gains):
            return True
        if monitor is not None and monitor.available:
            return any(self.monitor(monitor)) or self.mono(monitor) or self.pad(monitor)
        return False

    def revert(self, controller: MixController, monitor=None) -> list[str]:
        """Put the interface back to the saved setup. Returns notes about
        anything that could not be applied."""
        if not self.known:
            return []
        # Monitor buttons first: outputs that stop being followed get their own
        # level back before the saved levels are applied over them.
        if monitor is not None and monitor.available and self._monitor and monitor.fits(self._monitor):
            monitor.apply(self._monitor)
        notes = presets.apply(self.record, self.device, controller, force=self.device.untrusted)
        if monitor is not None and monitor.available:
            if self._monitor_saved:
                monitor.set_mono(bool(self._monitor_saved.get("mono", False)))
                monitor.set_pad(bool(self._monitor_saved.get("pad", False)))
        return notes
