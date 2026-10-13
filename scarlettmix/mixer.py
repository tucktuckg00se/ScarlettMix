"""MixControl-style mixes on top of the hardware gain matrix.

The interface itself only has a grid of gains (every mixer input into every
mix bus). Faders, pan, mute, solo, stereo mixes and a master per mix are an
application-level view of that grid, exactly as in the original software:
this module holds that view and computes the grid it implies.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import laws
from .device import Device, db_to_level, level_to_db

OFF_DB = laws.MIN_DB


@dataclass
class Strip:
    db: float = OFF_DB
    pan: float = 0.5
    mute: bool = False
    solo: bool = False


@dataclass
class Mix:
    """One mono mix (a single bus) or one stereo mix (a left/right bus pair)."""

    buses: list[int]
    strips: list[Strip]
    master_db: float = 0.0
    master_mute: bool = False

    @property
    def stereo(self) -> bool:
        return len(self.buses) == 2

    def bus_db(self, input_index: int, bus: int, linked: bool = False) -> float:
        """Gain in dB this mix asks of the hardware for one input into one bus.

        ``linked`` marks the input as half of a stereo channel: the pair shares
        the left input's strip, and in a stereo mix the left input feeds only
        the left bus and the right input only the right bus, with pan acting
        as balance between them.
        """
        strip = self.strips[input_index & ~1] if linked else self.strips[input_index]
        soloing = any(s.solo for s in self.strips)
        if self.master_mute or strip.mute or (soloing and not strip.solo):
            return OFF_DB
        if strip.db <= OFF_DB or self.master_db <= OFF_DB:
            return OFF_DB
        db = strip.db + self.master_db
        if self.stereo:
            left_bus = bus == self.buses[0]
            if linked and left_bus != (input_index % 2 == 0):
                return OFF_DB
            left, right = laws.pan_gains(strip.pan)
            db += left if left_bus else right
        return max(laws.MIN_DB, min(laws.MAX_DB, db))


@dataclass
class MixModel:
    n_inputs: int
    n_buses: int
    mixes: list[Mix] = field(default_factory=list)
    # Input pairs (0 = inputs 1/2, 1 = inputs 3/4, ...) joined as stereo
    # channels. As in the original, a link applies in every mix.
    stereo_inputs: set[int] = field(default_factory=set)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_levels(cls, n_inputs: int, n_buses: int,
                    levels: dict[tuple[int, int], int]) -> "MixModel":
        """All-mono view that reproduces the given hardware grid exactly."""
        mixes = [
            Mix([bus], [Strip(db=float(level_to_db(levels.get((i, bus), 0))))
                        for i in range(n_inputs)])
            for bus in range(n_buses)
        ]
        return cls(n_inputs, n_buses, mixes)

    def to_dict(self) -> dict:
        return {"n_inputs": self.n_inputs, "n_buses": self.n_buses,
                "stereo_inputs": sorted(self.stereo_inputs),
                "mixes": [asdict(m) for m in self.mixes]}

    @classmethod
    def from_dict(cls, data: dict, n_inputs: int, n_buses: int) -> "MixModel":
        """Load a saved view; raises ValueError if it does not fit this device."""
        mixes = []
        seen: list[int] = []
        for m in data.get("mixes", []):
            buses = [int(b) for b in m["buses"]]
            strips = [Strip(float(s["db"]), float(s.get("pan", 0.5)),
                            bool(s.get("mute")), bool(s.get("solo"))) for s in m["strips"]]
            if len(strips) != n_inputs or len(buses) not in (1, 2):
                raise ValueError("mix does not match this device")
            if len(buses) == 2 and (buses[0] % 2 or buses[1] != buses[0] + 1):
                raise ValueError("stereo mix must be an odd/even bus pair")
            seen += buses
            mixes.append(Mix(buses, strips, float(m.get("master_db", 0.0)),
                             bool(m.get("master_mute"))))
        if sorted(seen) != list(range(n_buses)):
            raise ValueError("mixes do not cover every bus exactly once")
        mixes.sort(key=lambda m: m.buses[0])
        linked = {int(p) for p in data.get("stereo_inputs", [])}
        if any(p < 0 or p * 2 + 1 >= n_inputs for p in linked):
            raise ValueError("stereo input pair does not exist on this device")
        return cls(n_inputs, n_buses, mixes, linked)

    # -- queries -----------------------------------------------------------

    def target_levels(self, mix: Mix | None = None) -> dict[tuple[int, int], int]:
        """Hardware grid values (0..134) implied by one mix, or by all of them."""
        out = {}
        for m in ([mix] if mix else self.mixes):
            for bus in m.buses:
                for i in range(self.n_inputs):
                    out[(i, bus)] = db_to_level(self.heard(m, i, bus))
        return out

    def heard(self, mix: Mix, input_index: int, bus: int) -> float:
        """Effective dB of one input in one bus, taking stereo channels into account."""
        return mix.bus_db(input_index, bus, input_index // 2 in self.stereo_inputs)

    def mix_for_bus(self, bus: int) -> Mix:
        return next(m for m in self.mixes if bus in m.buses)

    def pair_is_stereo(self, pair: int) -> bool:
        return self.mix_for_bus(pair * 2).stereo

    # -- stereo linking ----------------------------------------------------

    def set_pair_stereo(self, pair: int, stereo: bool) -> None:
        """Join two adjacent buses into a stereo mix, or split one apart.

        Both directions keep what is heard as close as possible: splitting
        is exact, joining turns left-only/right-only inputs into hard pans.
        The exception is stereo channels, whose two inputs share one level:
        after a split each mono mix gets both of them.
        """
        left_bus, right_bus = pair * 2, pair * 2 + 1
        if right_bus >= self.n_buses or self.pair_is_stereo(pair) == stereo:
            return
        if stereo:
            left, right = self.mix_for_bus(left_bus), self.mix_for_bus(right_bus)
            strips = []
            for i in range(self.n_inputs):
                l_db, r_db = self.heard(left, i, left_bus), self.heard(right, i, right_bus)
                if i // 2 in self.stereo_inputs:
                    strips.append(Strip(db=max(l_db, r_db)))
                    continue
                if l_db > OFF_DB and r_db <= OFF_DB:
                    pan = 0.0
                elif r_db > OFF_DB and l_db <= OFF_DB:
                    pan = 1.0
                else:
                    pan = 0.5
                strips.append(Strip(db=max(l_db, r_db), pan=pan))
            merged = [Mix([left_bus, right_bus], strips)]
        else:
            both = self.mix_for_bus(left_bus)
            def level(i: int, bus: int) -> float:
                if i // 2 in self.stereo_inputs:
                    return max(self.heard(both, j, bus) for j in (i & ~1, i | 1))
                return self.heard(both, i, bus)
            merged = [Mix([bus], [Strip(db=level(i, bus)) for i in range(self.n_inputs)])
                      for bus in (left_bus, right_bus)]
        keep = [m for m in self.mixes if left_bus not in m.buses and right_bus not in m.buses]
        self.mixes = sorted(keep + merged, key=lambda m: m.buses[0])

    def set_inputs_stereo(self, pair: int, stereo: bool) -> None:
        """Join two adjacent inputs into one stereo channel, or separate them.

        Joining gives both inputs the left one's level, mute and solo.
        Separating keeps what is heard: in stereo mixes the two inputs become
        hard-left and hard-right strips at the levels the balance gave them.
        """
        left, right = pair * 2, pair * 2 + 1
        if right >= self.n_inputs or (pair in self.stereo_inputs) == stereo:
            return
        for mix in self.mixes:
            a = mix.strips[left]
            if stereo:
                a.pan = 0.5
                mix.strips[right] = Strip(a.db, 0.5, a.mute, a.solo)
            elif mix.stereo:
                l_off, r_off = laws.pan_gains(a.pan)
                mix.strips[right] = Strip(max(OFF_DB, a.db + r_off) if a.db > OFF_DB else OFF_DB,
                                          1.0, a.mute, a.solo)
                a.db = max(OFF_DB, a.db + l_off) if a.db > OFF_DB else OFF_DB
                a.pan = 0.0
            else:
                mix.strips[right] = Strip(a.db, 0.5, a.mute, a.solo)
        (self.stereo_inputs.add if stereo else self.stereo_inputs.discard)(pair)

    def copy_mix(self, src: Mix, dst: Mix) -> None:
        """Give one mix the same strip and master settings as another."""
        dst.strips = [Strip(s.db, s.pan, s.mute, s.solo) for s in src.strips]
        dst.master_db, dst.master_mute = src.master_db, src.master_mute



class MixController:
    """Keeps the hardware gain grid in step with a MixModel."""

    def __init__(self, device: Device, saved: dict | None = None):
        self.device = device
        hw = device.matrix_levels()
        self.model = MixModel.from_levels(device.n_inputs, device.n_mixes, hw)
        self.restored = False
        if saved:
            # Only trust the saved view if it still describes what the
            # hardware is doing; never push stale settings at startup.
            try:
                candidate = MixModel.from_dict(saved, device.n_inputs, device.n_mixes)
            except (ValueError, KeyError, TypeError):
                candidate = None
            if candidate and candidate.target_levels() == hw:
                self.model = candidate
                self.restored = True

    def apply(self, mix: Mix | None = None) -> int:
        """Write whatever differs between the model and the hardware."""
        written = 0
        for key, level in self.model.target_levels(mix).items():
            e = self.device.matrix_gain.get(key)
            if e and self.device.set(e, [level]):
                written += 1
        return written

    def in_sync(self) -> bool:
        return self.model.target_levels() == self.device.matrix_levels()

    def adopt_hardware(self) -> None:
        """Another program changed the grid: rebuild the view from it."""
        self.model = MixModel.from_levels(self.device.n_inputs, self.device.n_mixes,
                                          self.device.matrix_levels())

    def replace_model(self, model: MixModel) -> int:
        self.model = model
        return self.apply()

