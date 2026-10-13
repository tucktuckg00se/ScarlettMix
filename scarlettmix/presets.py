"""Saving, loading and importing whole-device setups.

A preset is a plain JSON document: routing and level controls by name (enum
values by item name, so it survives driver changes), plus the mixer view.
Original Scarlett MixControl snapshot files (.18i20, .18i8, ...) can be
converted into the same form.
"""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET

from . import laws
from .alsa import TYPE_ENUMERATED
from .device import Device
from .mixer import OFF_DB, Mix, MixController, MixModel, Strip

FORMAT = "scarlettmix-preset"


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "scarlettmix")


def state_path(device: Device) -> str:
    return os.path.join(config_dir(), f"mixer-{device.backend.usb_id.replace(':', '-')}.json")


def load_state(device: Device) -> dict | None:
    try:
        with open(state_path(device)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_state(device: Device, model: MixModel) -> None:
    os.makedirs(config_dir(), exist_ok=True)
    tmp = state_path(device) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(model.to_dict(), f)
    os.replace(tmp, state_path(device))


def last_path(device: Device) -> str:
    return os.path.join(config_dir(), f"last-{device.backend.usb_id.replace(':', '-')}.json")


def save_last(device: Device) -> None:
    """Remember the interface's complete state, so it can be put back after a
    power cycle. This is hardware state only (the gain grid as raw levels);
    how the mixer window presents it is kept separately by save_state."""
    os.makedirs(config_dir(), exist_ok=True)
    tmp = last_path(device) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snapshot(device, None, "last session"), f)
    os.replace(tmp, last_path(device))


def restore_last(device: Device, controller: MixController | None = None,
                 force: bool = False) -> list[str] | None:
    """Reapply the remembered setup. Returns None if there is none."""
    try:
        with open(last_path(device)) as f:
            preset = json.load(f)
    except (OSError, ValueError):
        return None
    return apply(preset, device, controller, force)


def _preset_controls(device: Device):
    """Controls a preset carries, apart from the gain matrix (the mixer view owns that)."""
    elems = list(device.matrix_inputs) + list(device.capture)
    for o in device.outputs:
        elems += [e for e in (o.source_l, o.source_r, o.volume, o.switch) if e]
    elems += [e for e in (device.master_volume, device.master_switch, device.clock_source) if e]
    elems += device.input_switches
    return [e for e in elems if e.writable]


def snapshot(device: Device, model: MixModel | None, name: str = "") -> dict:
    """Describe the current setup. With a model the mixer is stored as faders,
    pan and links; without one, as the raw gain grid."""
    controls = {}
    for e in _preset_controls(device):
        values = device.get(e)
        controls[e.name] = ([e.items[v] for v in values] if e.type == TYPE_ENUMERATED else values)
    out = {"format": FORMAT, "version": 1, "name": name, "device": device.backend.usb_id,
           "controls": controls}
    if model is not None:
        out["mixer"] = model.to_dict()
    else:
        out["matrix"] = {e.name: device.get(e)[0] for e in device.matrix_gain.values()}
    return out


def apply(preset: dict, device: Device, controller: MixController | None = None,
          force: bool = False) -> list[str]:
    """Apply a preset; returns human-readable notes about anything skipped."""
    if preset.get("format") != FORMAT:
        raise ValueError("not a scarlettmix preset")
    notes = []
    allowed = {e.name: e for e in _preset_controls(device)}
    for name, values in preset.get("controls", {}).items():
        e = allowed.get(name)
        if not e:
            notes.append(f"{name}: not available on this device")
            continue
        if e.type == TYPE_ENUMERATED:
            if any(v not in e.items for v in values):
                notes.append(f"{name}: '{values[0]}' is not selectable here")
                continue
            values = [e.items.index(v) for v in values]
        device.set(e, list(values), force)
    if "mixer" in preset:
        try:
            model = MixModel.from_dict(preset["mixer"], device.n_inputs, device.n_mixes)
        except (ValueError, KeyError, TypeError) as err:
            notes.append(f"mixer: {err}")
        else:
            if controller is not None:
                controller.replace_model(model)
            else:  # no mixer view to update: just set the gains it implies
                for key, level in model.target_levels().items():
                    device.set(device.matrix_gain[key], [level], force)
    elif "matrix" in preset:
        cells = {e.name: e for e in device.matrix_gain.values()}
        for name, value in preset["matrix"].items():
            if name in cells:
                device.set(cells[name], [int(value)], force)
        if controller is not None and not controller.in_sync():
            controller.adopt_hardware()
    return notes


def save_file(path: str, preset: dict) -> None:
    with open(path, "w") as f:
        json.dump(preset, f, indent=1)


def load_file(path: str, device: Device) -> tuple[dict, list[str]]:
    """Load either our JSON or an original MixControl snapshot."""
    with open(path, "rb") as f:
        head = f.read(64).lstrip()
    if head.startswith(b"<"):
        return import_mixcontrol(path, device)
    with open(path) as f:
        return json.load(f), []


# -- original MixControl snapshots -----------------------------------------

# Signal "type" numbers used in the original's router cross-points.
_SRC_ITEM = {0: "Analog", 1: "ADAT", 2: "SPDIF", 3: "PCM"}


def _source_item(sig_type: int, index: int, device: Device) -> str:
    if sig_type == 5:
        return f"Mix {chr(ord('A') + index)}"
    if sig_type in _SRC_ITEM:
        return f"{_SRC_ITEM[sig_type]} {index + 1}"
    return "Off"


def import_mixcontrol(path: str, device: Device) -> tuple[dict, list[str]]:
    root = ET.parse(path).getroot()
    notes = []
    controls: dict[str, list] = {}

    # Destinations by the original's type number: 0 analog out, 2 S/PDIF out,
    # 1 ADAT out, 3 to-host (capture), 5 mixer input. Physical outputs are
    # numbered analog, then S/PDIF, then ADAT in the driver.
    outs = device.output_sources()
    n_analog = sum(2 for o in device.outputs if "SPDIF" not in o.label and "ADAT" not in o.label)
    n_spdif = sum(2 for o in device.outputs if "SPDIF" in o.label)
    out_base = {0: 0, 2: n_analog, 1: n_analog + n_spdif}

    router = next((el for el in root if el.tag.endswith("Router")), None)
    for xp in (router.iter("xp") if router is not None else []):
        try:
            st, si, dt, di = (int(t) for t in xp.text.split())
        except (ValueError, AttributeError):
            continue
        if dt in out_base:
            idx = out_base[dt] + di
            e = outs[idx][1] if idx < len(outs) else None
        elif dt == 3:
            e = device.capture[di] if di < len(device.capture) else None
        elif dt == 5:
            e = device.matrix_inputs[di] if di < len(device.matrix_inputs) else None
        else:
            e = None
        if not e:
            continue
        item = _source_item(st, si, device)
        if item in e.items:
            controls[e.name] = [item]
        else:
            notes.append(f"{e.name}: source '{item}' is not selectable on Linux")

    mixer_el = next((el for el in root if el.tag.endswith("Mixer")), None)
    mixer = None
    if mixer_el is not None:
        mixer = _import_mixer(mixer_el, device).to_dict()

    clock = root.find(".//FS")
    if clock is not None:
        notes.append(f"sample rate ({clock.text} Hz) is set by the audio server on Linux, not imported")
    notes.append("hardware monitor-knob assignment is not exposed by the Linux driver, not imported")

    preset = {"format": FORMAT, "version": 1, "name": root.get("name", ""),
              "device": device.backend.usb_id, "controls": controls}
    if mixer:
        preset["mixer"] = mixer
    return preset, notes


def _import_mixer(mixer_el, device: Device) -> MixModel:
    n_in, n_bus = device.n_inputs, device.n_mixes
    globals_el = mixer_el.find("globals")
    chan_stereo = int(globals_el.get("chanms", "0")) if globals_el is not None else 0
    bus_stereo = int(globals_el.get("busms", "0")) if globals_el is not None else 0

    by_index = {int(m.get("m")): m for m in mixer_el.iter("mix")}
    mixes = []
    bus = 0
    while bus < n_bus:
        stereo = bus + 1 < n_bus and (bus_stereo >> bus) & 3 == 3
        el = by_index.get(bus)
        strips = [Strip() for _ in range(n_in)]
        master_db, master_mute = OFF_DB, False
        if el is not None:
            master_db = round(laws.fader_to_db(float(el.get("gain", "0"))), 3) + 0.0
            master_mute = el.get("mute") == "1"
            for ch in el.iter("chan"):
                c = int(ch.get("c"))
                if c >= n_in:
                    continue
                linked = (chan_stereo >> (c & ~1)) & 3 == 3
                # A stereo channel stores balance in "b"; a mono one stores pan in "p".
                pan = float(ch.get("b" if linked else "p", "0.5"))
                strips[c] = Strip(db=round(laws.fader_to_db(float(ch.get("g", "0"))), 3) + 0.0, pan=pan,
                                  mute=ch.get("m") == "1", solo=ch.get("s") == "1")
        if stereo:
            mixes.append(Mix([bus, bus + 1], strips, master_db, master_mute))
            bus += 2
        else:
            mixes.append(Mix([bus], strips, master_db, master_mute))
            bus += 1
    linked_pairs = {p for p in range(n_in // 2) if (chan_stereo >> (p * 2)) & 3 == 3}
    return MixModel(n_in, n_bus, mixes, linked_pairs)
