import json
import os

import pytest

from scarlettmix import laws, presets
from scarlettmix.backend import FakeBackend
from scarlettmix.device import Device, db_to_level, level_to_db
from scarlettmix.mixer import MixController, MixModel, Strip

DEFAULT_SNAPSHOT = os.path.join(os.path.dirname(__file__), "data", "Default.18i20")


@pytest.fixture
def device():
    return Device(FakeBackend())


def level(device, i, bus):
    return device.get(device.matrix_gain[(i, bus)])[0]


# -- gain laws ---------------------------------------------------------------

def test_fader_law_matches_original_unity_position():
    # 0.775137 is the value the original writes for a fader at 0 dB.
    assert laws.fader_to_db(0.775137) == pytest.approx(0.0, abs=1e-3)
    assert laws.fader_to_db(0.0) == -128.0
    assert laws.fader_to_db(1.0) == 6.0


@pytest.mark.parametrize("db", [-128, -90, -40, -12, -1, 0, 3, 6])
def test_fader_law_round_trips(db):
    assert laws.fader_to_db(laws.db_to_fader(db)) == pytest.approx(db, abs=1e-6)


def test_pan_is_a_balance_law():
    assert laws.pan_gains(0.5) == (0.0, 0.0)
    left, right = laws.pan_gains(0.0)
    assert left == 0.0 and right <= -128
    left, right = laws.pan_gains(0.75)
    assert right == 0.0 and -128 < left < 0


# -- device discovery --------------------------------------------------------

def test_18i20_structure(device):
    assert device.model_name == "Scarlett 18i20"
    assert (device.n_inputs, device.n_mixes) == (18, 8)
    assert len(device.matrix_gain) == 18 * 8
    assert [o.label for o in device.outputs][:2] == ["Monitor", "Line 3/4"]
    assert len(device.output_sources()) == 20
    assert len(device.capture) == 18
    assert device.clock_source.items == ["Internal", "SPDIF", "ADAT"]
    # Mixer inputs cannot select a mix (no feedback path); outputs can.
    assert "Mix A" not in device.matrix_inputs[0].items
    assert "Mix A" in device.outputs[0].source_l.items


def test_set_skips_unchanged_values(device):
    e = device.matrix_gain[(0, 0)]
    assert device.set(e, device.get(e)) is False
    assert device.backend.writes == []
    assert device.set(e, [5]) is True
    assert device.backend.writes == [(e.name, [5])]


def test_level_conversion():
    assert level_to_db(128) == 0
    assert db_to_level(0) == 128
    assert db_to_level(-500) == 0
    assert db_to_level(99) == 134


def test_external_change_notifies(device):
    e = device.outputs[0].volume
    device.get(e)
    seen = []
    device.add_listener(seen.append)
    device.backend._values[e.numid] = [10, 10]
    device.backend.drain_events = lambda: ({e.numid}, False)
    assert device.poll_events() is True
    assert seen == [e] and device.get(e) == [10, 10]


# -- mixer view --------------------------------------------------------------

def test_startup_never_writes(device):
    controller = MixController(device)
    assert controller.in_sync()
    assert controller.apply() == 0
    assert device.backend.writes == []


def test_stale_saved_state_is_ignored(device):
    saved = MixModel.from_levels(18, 8, {}).to_dict()  # everything off: not what the device has
    controller = MixController(device, saved)
    assert not controller.restored
    assert controller.in_sync() and device.backend.writes == []


def test_matching_saved_state_is_restored(device):
    first = MixController(device)
    first.model.set_pair_stereo(0, True)
    first.apply()
    again = MixController(device, first.model.to_dict())
    assert again.restored and again.model.pair_is_stereo(0)


def test_mute_solo_and_master(device):
    c = MixController(device)
    mix = c.model.mix_for_bus(2)
    mix.strips[0].db, mix.strips[1].db = -6.0, -10.0
    c.apply(mix)
    assert (level(device, 0, 2), level(device, 1, 2)) == (122, 118)
    mix.strips[1].solo = True
    c.apply(mix)
    assert (level(device, 0, 2), level(device, 1, 2)) == (0, 118)
    mix.strips[1].solo = False
    mix.strips[0].mute = True
    mix.master_db = -3.0
    c.apply(mix)
    assert (level(device, 0, 2), level(device, 1, 2)) == (0, 115)
    mix.master_mute = True
    c.apply(mix)
    assert (level(device, 0, 2), level(device, 1, 2)) == (0, 0)


def test_gain_is_clamped_to_hardware_range(device):
    c = MixController(device)
    mix = c.model.mix_for_bus(2)
    mix.strips[0].db, mix.master_db = 6.0, 6.0
    c.apply(mix)
    assert level(device, 0, 2) == 134


def test_stereo_join_and_split_preserve_hardware(device):
    c = MixController(device)
    mix_l, mix_r = c.model.mix_for_bus(2), c.model.mix_for_bus(3)
    mix_l.strips[0].db = 0.0     # left only
    mix_r.strips[1].db = -4.0    # right only
    mix_l.strips[2].db = mix_r.strips[2].db = -9.0  # both
    c.apply()
    before = device.matrix_levels()
    c.model.set_pair_stereo(1, True)
    stereo = c.model.mix_for_bus(2)
    assert [s.pan for s in stereo.strips[:3]] == [0.0, 1.0, 0.5]
    assert c.apply() == 0 and device.matrix_levels() == before
    c.model.set_pair_stereo(1, False)
    assert c.apply() == 0 and device.matrix_levels() == before


def test_pan_attenuates_far_side_only(device):
    c = MixController(device)
    c.model.set_pair_stereo(1, True)
    mix = c.model.mix_for_bus(2)
    mix.strips[0].db, mix.strips[0].pan = 0.0, 0.25
    c.apply(mix)
    assert level(device, 0, 2) == 128
    assert level(device, 0, 3) == 128 + round(laws.balance_db(0.25))


def test_model_validation():
    good = MixModel.from_levels(18, 8, {}).to_dict()
    MixModel.from_dict(good, 18, 8)
    with pytest.raises(ValueError):
        MixModel.from_dict(good, 16, 8)
    bad = json.loads(json.dumps(good))
    bad["mixes"].pop()
    with pytest.raises(ValueError):
        MixModel.from_dict(bad, 18, 8)
    odd = {"mixes": [{"buses": [1, 2], "strips": [vars(Strip())] * 18}]}
    with pytest.raises(ValueError):
        MixModel.from_dict(odd, 18, 8)


def test_adopt_hardware_after_external_change(device):
    c = MixController(device)
    c.model.set_pair_stereo(0, True)
    c.apply()
    e = device.matrix_gain[(0, 0)]
    device.backend._values[e.numid] = [90]
    device._cache.pop(e.numid)
    assert not c.in_sync()
    c.adopt_hardware()
    assert c.in_sync() and not c.model.pair_is_stereo(0)
    assert c.model.mix_for_bus(0).strips[0].db == -38.0


# -- presets -----------------------------------------------------------------

def test_import_original_default_snapshot(device):
    preset, notes = presets.load_file(DEFAULT_SNAPSHOT, device)
    c = MixController(device)
    assert presets.apply(preset, device, c) == []
    assert len(preset["controls"]) == 56          # every cross-point in the file
    out = device.outputs[0]
    assert (device.item(out.source_l), device.item(out.source_r)) == ("Mix A", "Mix B")
    assert device.item(device.capture[0]) == "Analog 1"
    assert device.item(device.capture[8]) == "SPDIF 1"
    assert device.item(device.matrix_inputs[16]) == "PCM 1"
    assert [m.buses for m in c.model.mixes] == [[0, 1], [2, 3], [4, 5], [6, 7]]
    # Analog 1 centred into Mix A/B at unity; DAW 1/2 as a hard-panned stereo pair.
    assert (level(device, 0, 0), level(device, 0, 1)) == (128, 128)
    assert (level(device, 16, 0), level(device, 16, 1)) == (128, 0)
    assert (level(device, 17, 0), level(device, 17, 1)) == (0, 128)
    assert any("sample rate" in n for n in notes)


def test_preset_round_trip(device, tmp_path):
    c = MixController(device)
    c.model.set_pair_stereo(2, True)
    mix = c.model.mix_for_bus(4)
    mix.strips[3].db, mix.strips[3].pan, mix.master_db = -7.0, 0.3, -2.0
    c.apply()
    device.set_item(device.outputs[1].source_l, "Mix E")
    device.set(device.outputs[1].volume, [100, 100])
    path = tmp_path / "p.json"
    presets.save_file(str(path), presets.snapshot(device, c.model, "p"))

    other = Device(FakeBackend())
    c2 = MixController(other)
    preset, notes = presets.load_file(str(path), other)
    assert notes == [] and presets.apply(preset, other, c2) == []
    for e in device.elements:
        if e.writable and e.index == 0:
            assert other.get(other.by_name[e.name]) == device.get(e), e.name
    assert c2.model.to_dict() == c.model.to_dict()


def test_apply_reports_what_it_cannot_do(device):
    c = MixController(device)
    preset = {"format": presets.FORMAT, "controls": {
        "No Such Control": [1],
        device.matrix_inputs[0].name: ["Mix A"],   # not selectable for mixer inputs
        device.capture[0].name: ["Analog 2"],
    }}
    notes = presets.apply(preset, device, c)
    assert len(notes) == 2
    assert device.item(device.capture[0]) == "Analog 2"
    with pytest.raises(ValueError):
        presets.apply({"format": "something else"}, device, c)


def test_state_file_location(device, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    c = MixController(device)
    presets.save_state(device, c.model)
    assert os.path.exists(tmp_path / "scarlettmix" / "mixer-1235-800c.json")
    assert presets.load_state(device) == c.model.to_dict()



def test_original_names(device):
    from scarlettmix import names
    assert names.source("PCM 7") == "DAW 7" and names.source("Mix C") == "Mix 3"
    assert names.source("Analog 2") == "Analog 2"
    labels = [l for l, _ in device.output_sources()]
    assert labels[:3] == ["Monitor Output 1", "Monitor Output 2", "Line Output 3"]
    assert labels[10:12] == ["SPDIF Output 1", "SPDIF Output 2"] and labels[-1] == "ADAT Output 8"
    by_label = {o.label: o for o in device.outputs}
    assert device.headphones(by_label["Line 7/8"]) == 1
    assert device.headphones(by_label["Line 9/10"]) == 2
    assert device.headphones(by_label["Monitor"]) is None


def test_send_mix_to_outputs(device):
    hp1 = next(o for o in device.outputs if o.label == "Line 7/8")
    device.send_mix([2, 3], hp1, True)                       # stereo Mix 3/4 to Headphones 1
    assert (device.item(hp1.source_l), device.item(hp1.source_r)) == ("Mix C", "Mix D")
    assert device.mix_sent_to([2, 3], hp1) and not device.mix_sent_to([2], hp1)
    device.send_mix([4], hp1, True)                          # a mono mix feeds both sides
    assert (device.item(hp1.source_l), device.item(hp1.source_r)) == ("Mix E", "Mix E")
    assert device.mix_sent_to([4], hp1) and not device.mix_sent_to([2, 3], hp1)
    device.send_mix([2, 3], hp1, False)                      # not playing it: leave alone
    assert device.item(hp1.source_l) == "Mix E"
    device.send_mix([4], hp1, False)
    assert (device.item(hp1.source_l), device.item(hp1.source_r)) == ("Off", "Off")


def test_stereo_channel_in_stereo_and_mono_mixes(device):
    c = MixController(device)
    c.model.set_pair_stereo(1, True)                     # Mix 3/4 stereo, Mix 5 mono
    c.model.set_inputs_stereo(2, True)                   # inputs 5/6 as one channel
    assert c.model.stereo_inputs == {2}
    st, mono = c.model.mix_for_bus(2), c.model.mix_for_bus(4)
    st.strips[4].db = -6.0
    mono.strips[4].db = -10.0
    c.apply()
    # stereo mix: left input to the left bus only, right input to the right bus only
    assert [level(device, 4, 2), level(device, 4, 3)] == [122, 0]
    assert [level(device, 5, 2), level(device, 5, 3)] == [0, 122]
    # mono mix: both inputs at the shared level
    assert (level(device, 4, 4), level(device, 5, 4)) == (118, 118)
    st.strips[4].pan = 0.25                              # balance toward the left
    c.apply(st)
    assert level(device, 4, 2) == 122 and level(device, 5, 3) == 122 + round(laws.balance_db(0.25))
    st.strips[4].mute = True
    c.apply(st)
    assert level(device, 4, 2) == 0 and level(device, 5, 3) == 0


def test_unlinking_stereo_channel_keeps_levels(device):
    c = MixController(device)
    c.model.set_pair_stereo(1, True)
    c.model.set_inputs_stereo(2, True)
    c.model.mix_for_bus(2).strips[4].db = -6.0
    c.model.mix_for_bus(2).strips[4].pan = 0.3
    c.model.mix_for_bus(4).strips[4].db = -10.0
    c.apply()
    before = device.matrix_levels()
    c.model.set_inputs_stereo(2, False)
    assert c.model.stereo_inputs == set()
    assert c.apply() == 0 and device.matrix_levels() == before
    restored = MixModel.from_dict(c.model.to_dict(), 18, 8)
    assert restored.target_levels() == before


def test_stereo_inputs_saved_and_validated():
    m = MixModel.from_levels(18, 8, {})
    m.set_inputs_stereo(8, True)
    again = MixModel.from_dict(m.to_dict(), 18, 8)
    assert again.stereo_inputs == {8}
    bad = m.to_dict(); bad["stereo_inputs"] = [9]
    with pytest.raises(ValueError):
        MixModel.from_dict(bad, 18, 8)
    old = m.to_dict(); del old["stereo_inputs"]          # files written before this feature
    assert MixModel.from_dict(old, 18, 8).stereo_inputs == set()


def test_copy_mix(device):
    c = MixController(device)
    c.model.set_pair_stereo(1, True)
    src, dst = c.model.mix_for_bus(2), c.model.mix_for_bus(4)   # stereo -> mono
    src.strips[0].db, src.strips[0].pan, src.strips[1].mute = -5.0, 0.2, True
    src.strips[1].db, src.master_db = -3.0, -2.0
    c.model.copy_mix(src, dst)
    c.apply(dst)
    assert dst.master_db == -2.0 and dst.strips[0].db == -5.0 and dst.strips[1].mute
    assert (level(device, 0, 4), level(device, 1, 4)) == (128 - 7, 0)
    dst.strips[0].db = 0.0                                # copies are independent
    assert src.strips[0].db == -5.0


def test_default_snapshot_imports_daw_pair_as_stereo_channel(device):
    preset, _ = presets.load_file(DEFAULT_SNAPSHOT, device)
    c = MixController(device)
    presets.apply(preset, device, c)
    assert c.model.stereo_inputs == {8}                   # mixer inputs 17/18 = DAW 1/2
    assert c.model.mix_for_bus(0).strips[16].pan == 0.5







def test_preset_with_mixer_view_applies_without_a_controller(device):
    c = MixController(device)
    c.model.mix_for_bus(5).strips[2].db = -9.0
    c.apply()
    preset = presets.snapshot(device, c.model)
    other = Device(FakeBackend())
    assert presets.apply(preset, other) == []
    assert other.matrix_levels() == device.matrix_levels()


def test_forced_write_reaches_the_device_even_when_value_looks_unchanged(device):
    cell = device.matrix_gain[(0, 0)]
    want = device.get(cell)
    assert device.set(cell, want) is False and device.backend.writes == []
    device.set(cell, want, force=True)
    assert [v for _, v in device.backend.writes] == [[want[0] - 1], want]
    sw = device.outputs[0].switch
    device.backend.writes.clear()
    device.set(sw, [1, 1], force=True)
    assert [v for _, v in device.backend.writes] == [[0, 0], [1, 1]]
    route = device.matrix_inputs[0]                       # routing reads back reliably
    device.backend.writes.clear()
    device.set(route, device.get(route), force=True)
    assert device.backend.writes == []


# -- patched-driver controls ---------------------------------------------------

@pytest.fixture
def xdevice():
    return Device(FakeBackend(extras=True))


def test_extras_absent_on_stock_driver(device):
    from scarlettmix.monitor import Monitor
    assert not device.has_meters and not device.has_monitor and device.hw_save is None
    assert not Monitor(device).available


def test_meters_and_status(xdevice):
    b = xdevice.backend
    b._values[xdevice.meter_pcm.numid][19] = 65535
    b._values[xdevice.meter_pcm.numid][18] = 6553
    b._values[xdevice.meter_inputs.numid][0] = 655
    m = xdevice.read_meters()
    assert (len(m["inputs"]), len(m["mixes"]), len(m["pcm"])) == (18, 8, 20)
    assert m["pcm"][19] == pytest.approx(0.0) and m["pcm"][18] == pytest.approx(-20.0, abs=0.01)
    assert m["inputs"][0] == pytest.approx(-40.0, abs=0.01) and m["mixes"][0] <= -100
    assert xdevice.read_status() == (30, False, False)
    xdevice.write(xdevice.hw_dim, [1])
    assert xdevice.read_status() == (30, False, True)
    assert xdevice.source_meter("PCM 3") == ("pcm", 2)
    assert xdevice.source_meter("Analog 1") == ("inputs", 0)
    assert xdevice.source_meter("SPDIF 2") == ("inputs", 9)
    assert xdevice.source_meter("ADAT 1") == ("inputs", 10)
    assert xdevice.source_meter("Off") is None and xdevice.source_meter("Mix A") is None


def test_save_to_hardware(xdevice):
    xdevice.save_to_hardware()
    assert xdevice.backend.saves == 1 and xdevice.backend.read(xdevice.hw_save) == [0]






def test_monitor_presets_from_the_original(xdevice):
    from scarlettmix.monitor import BLUE, SILVER, Monitor
    mon = Monitor(xdevice)
    table = dict(mon.presets())
    assert list(table)[:3] == ["Mono", "Stereo", "Quad"] and table["5.1 Surround"] == [1, 2, 3, 4, 7, 8]
    mon.use_preset(table["Mid+Phones 1"])
    assert mon.states == [SILVER] * 6 + [BLUE, BLUE] + [SILVER] * 2
    assert xdevice.get(xdevice.hw_masks[0]) == [0] and xdevice.get(xdevice.hw_masks[1]) == [0b0011000000]
    assert not Monitor(Device(FakeBackend())).available         # menu is not shown on the stock driver






# -- what the driver cannot be believed about ------------------------------------

def test_restart_shows_saved_setup_without_touching_the_unit(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    b = FakeBackend()
    d = Device(b)
    assert not presets.is_trusted(d)                     # never synced
    c = MixController(d)
    c.model.mix_for_bus(4).strips[2].db = -9.0
    c.apply()
    d.set(d.outputs[2].volume, [101, 101])
    presets.sync_all(d); presets.mark_synced(d)
    assert presets.is_trusted(d)
    presets.save_saved(d, c.model)                       # as at Save to Hardware
    truth = {e.numid: d.get(e) for e in d.elements if e.writable}

    b.power_cycle(misreport=True)                        # driver now reads 0 dB / muted everywhere
    b.writes.clear()
    d2 = Device(b)
    assert not presets.is_trusted(d2)
    cell = d2.matrix_gain[(9, 7)]
    assert d2.get(cell) == [128] and truth[cell.numid] == [0]     # the wrong reading
    d2._cache.clear(); d2.untrusted = True
    presets.prime(d2, presets.load_saved(d2))
    assert b.writes == []                                # nothing sent
    for e in d2.elements:
        if e.writable and not e.items and e.name in d2.by_name and "Extension" not in e.name:
            assert d2.get(e) == truth[e.numid], e.name   # shown as saved, not as misread
    assert MixController(d2, presets.load_saved(d2)["mixer"]).restored

    d2.set(cell, [0])                                    # "no change" by the wrong reading's logic...
    assert [v for n, v in b.writes if n == cell.name] == [[1], [0]]   # ...is still sent
    d2.refresh(d2.outputs[2].volume)
    assert d2.get(d2.outputs[2].volume) == [101, 101]    # a re-read does not bring the nonsense back

    b.writes.clear()
    assert presets.sync_all(d2) == len(b.writes) // 2 > 150
    d2.untrusted = False; presets.mark_synced(d2)
    assert presets.is_trusted(Device(b))
    b.power_cycle()
    assert not presets.is_trusted(Device(b))


def test_saved_snapshot_is_only_what_was_saved(device, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert presets.load_saved(device) is None
    c = MixController(device)
    from scarlettmix.saved import Saved
    presets.save_saved(device, c.model)
    assert not Saved(device, None).known and not Saved(device, None).any()
    snap = presets.load_saved(device)
    saved = Saved(device, snap)
    vol = device.outputs[0].volume
    assert saved.known and not saved.any() and not saved.control(vol)
    device.set(vol, [90, 90])                            # changed afterwards, not saved
    assert presets.load_saved(device)["controls"][vol.name] != [90, 90]
    assert saved.any() and saved.control(vol) and not saved.control(device.outputs[1].volume)
    device.set(vol, snap["controls"][vol.name])
    assert not saved.any()
    device.set(device.matrix_gain[(5, 3)], [100])        # a mixer gain counts too
    assert saved.any() and saved.cell(5, 3) and not saved.cell(5, 2)


def test_real_connection_identity_parts(monkeypatch):
    """A simulated restart changes the identity; an untracked backend has none."""
    from scarlettmix.backend import Backend
    b = FakeBackend()
    first = b.connection_id(); b.power_cycle()
    assert b.connection_id() != first and Backend().connection_id() == ""



# -- monitor section: a window onto the interface ---------------------------------

def _lists(d):
    return [d.backend.read(e)[0] for e in d.hw_masks]


def test_monitor_is_read_from_the_interface_and_reading_sends_nothing(xdevice):
    from scarlettmix.monitor import BLUE, RED, SILVER, Monitor
    b = xdevice.backend
    b.write(xdevice.hw_masks[1], [0b11000011]); b.write(xdevice.hw_masks[2], [0b11000011])
    hp2 = next(o for o in xdevice.outputs if o.label == "Line 9/10")
    xdevice.set(hp2.switch, [0, 1])
    b.writes.clear()
    mon = Monitor(xdevice)
    assert mon.states == [BLUE, BLUE] + [SILVER] * 4 + [BLUE, BLUE, RED, SILVER]
    assert [mon.on_knob(i) for i in (0, 1, 6, 7)] == [True, True, False, False]
    assert mon.pending() == [6, 7]                       # on the Mute/Dim lists, not the knob's
    assert mon.refresh() is False and b.writes == []     # looking never writes
    assert not hasattr(mon, "follow") and not hasattr(mon, "save")   # no notes, no following


def test_click_changes_only_that_output(xdevice):
    from scarlettmix.monitor import BLUE, RED, SILVER, Monitor
    b = xdevice.backend
    mon = Monitor(xdevice)
    levels = {o.label: xdevice.get(o.volume) for o in xdevice.outputs if o.volume}
    b.writes.clear()
    mon.set_state(2, BLUE)                               # output 3 joins the group
    assert b.writes == [("Monitor Mask 0x0b", [0b111]), ("Monitor Mask 0x0c", [0b111])]
    assert _lists(xdevice) == [3, 7, 7] and mon.pending() == [2]
    assert {o.label: xdevice.get(o.volume) for o in xdevice.outputs if o.volume} == levels

    b.writes.clear()
    mon.set_state(2, SILVER)
    assert [n for n, _ in b.writes] == ["Monitor Mask 0x0b", "Monitor Mask 0x0c"]
    assert _lists(xdevice) == [3, 3, 3]

    line34 = next(o for o in xdevice.outputs if o.label == "Line 3/4")
    b.writes.clear()
    mon.set_state(3, RED)                                # output 4 muted: its own switch only
    assert b.writes == [(line34.switch.name, [1, 0])] and mon.states[3] == RED
    mon.set_state(3, BLUE)
    assert xdevice.get(line34.switch) == [1, 1] and _lists(xdevice) == [3, 11, 11]


def test_knob_takes_an_output_after_save_and_restart(xdevice):
    from scarlettmix.monitor import BLUE, SILVER, Monitor
    b = xdevice.backend
    mon = Monitor(xdevice)
    hp1 = next(o for o in xdevice.outputs if o.label == "Line 7/8")
    own = xdevice.get(hp1.volume)
    mon.set_state(6, BLUE); mon.set_state(7, BLUE)
    assert mon.pending() == [6, 7] and _lists(xdevice)[0] == 3      # running unit's knob list untouched
    assert mon.save_to_hardware() is True and b.saves == 1
    assert b._saved_masks == [0b11000011] * 3 and _lists(xdevice)[0] == 3
    assert xdevice.get(hp1.volume) == own                # nothing moved its level

    b.power_cycle()
    b.writes.clear()
    after = Monitor(xdevice)
    assert after.pending() == [] and after.on_knob(6) and b.writes == []

    after.set_state(6, SILVER); after.set_state(7, SILVER)          # taken off the knob again
    names = [n for n, _ in b.writes]
    assert names.index(hp1.volume.name) < names.index("Monitor Mask 0x0a")   # level first: no jump
    assert xdevice.get(hp1.volume) == [98, 98]           # the knob position (-30 in the simulator)
    assert _lists(xdevice) == [3, 3, 3]
    after.set_state(6, BLUE)
    assert _lists(xdevice)[0] == 3 and after.pending() == [6]       # never added back while running


def test_mono_pad_and_presets(xdevice):
    from scarlettmix.monitor import BLUE, Monitor
    mon = Monitor(xdevice)
    mon.set_mono(True)
    assert [xdevice.backend.read(e)[0] for e in xdevice.hw_mono] == [1, 0, 0, 0, 0]
    mon.use_preset([1, 2, 3, 4, 7])                      # 7 without 8: that pair is not mono
    assert [xdevice.backend.read(e)[0] for e in xdevice.hw_mono] == [1, 1, 0, 0, 0] and mon.mono
    mon.set_mono(False)
    assert not mon.mono and not any(xdevice.backend.read(e)[0] for e in xdevice.hw_mono)
    mon.set_pad(True)
    assert mon.pad and xdevice.backend.read(xdevice.hw_pad) == [1]
    assert Monitor(xdevice).pad and Monitor(xdevice).states[6] == BLUE   # all of it read back


def test_unreadable_mutes_are_not_drawn_red(tmp_path):
    """After a restart the driver reports every output as muted. With no
    saved setup to show instead, that must not paint every button red."""
    from scarlettmix.monitor import RED, Monitor
    b = FakeBackend(extras=True)
    b.power_cycle(misreport=True)
    d = Device(b); d.untrusted = True
    assert RED not in Monitor(d).states
    d.untrusted = False
    assert Monitor(d).states.count(RED) == 10            # the same readings, if they were believed


def test_saved_indicator_follows_the_monitor_group(xdevice):
    from scarlettmix.monitor import BLUE, Monitor
    mon = Monitor(xdevice); c = MixController(xdevice)
    from scarlettmix.saved import Saved
    saved = Saved(xdevice, presets.saved_record(xdevice, c.model, mon))
    assert not saved.any(mon) and saved.monitor(mon) == [False] * 10
    mon.set_state(4, BLUE)
    assert saved.any(mon) and saved.monitor(mon) == [False] * 4 + [True] + [False] * 5
    mon.set_mono(True)
    assert saved.mono(mon) and not saved.pad(mon)


def test_a_click_never_acts_on_what_is_shown_for_other_outputs():
    """The driver misreads every mute after the unit re-appears. Clicking one
    button must not take the others out of the group or off the knob because
    they were (wrongly) drawn red."""
    from scarlettmix.monitor import BLUE, RED, SILVER, Monitor
    b = FakeBackend(extras=True)
    for name in ("Monitor Mask 0x0b", "Monitor Mask 0x0c"):
        b.write(b.by_name[name], [0b11000111])
    b.power_cycle(misreport=True); b._values[b._mask_ids[0]] = [3]
    for i in b._mask_ids[1:]:
        b._values[i] = [0b11000111]
    d = Device(b)                                        # nobody marked it untrusted
    mon = Monitor(d)
    assert mon.states.count(RED) == 10                   # the wrong picture
    monitor_level = d.get(d.outputs[0].volume)
    b.writes.clear()
    mon.set_state(2, SILVER)                             # output 3 leaves the group
    assert [b.read(e)[0] for e in d.hw_masks] == [3, 0b11000011, 0b11000011]
    assert d.get(d.outputs[0].volume) == monitor_level   # mains not released from the knob
    assert all(n.startswith("Monitor Mask 0x0") or "Line 3/4" in n for n, _ in b.writes)


def test_prepare_marks_a_fresh_unit_untrusted(tmp_path):
    b = FakeBackend(extras=True)
    d = Device(b)
    assert presets.prepare(d) is None and d.untrusted and not d.primed
    presets.mark_synced(d)
    d2 = Device(b)
    assert presets.prepare(d2) is None and not d2.untrusted


# -- saved or not, for every control ----------------------------------------------

def test_strip_marks_and_revert(xdevice):
    """Each kind of mixer change is reported for its own control only, and
    revert puts the interface back to exactly what was saved."""
    from scarlettmix.monitor import BLUE, RED, Monitor
    from scarlettmix.saved import Saved
    d = xdevice
    c = MixController(d); mon = Monitor(d)
    saved = Saved(d, presets.saved_record(d, c.model, mon))
    before = {e.numid: d.get(e) for e in d.elements if e.writable}
    mix = c.model.mix_for_bus(0)
    assert saved.strip(mix, 0, [0]) == {"fader": False, "pan": False, "mute": False, "solo": False}

    mix.strips[0].db = -12.0; c.apply(mix)
    assert saved.strip(mix, 0, [0])["fader"] and not saved.strip(mix, 1, [1])["fader"]
    assert saved.mix(mix) and not saved.mix(c.model.mix_for_bus(3))
    mix.strips[1].mute = True; mix.master_db = -3.0; c.apply(mix)
    assert saved.strip(mix, 1, [1]) == {"fader": False, "pan": False, "mute": True, "solo": False}
    assert saved.master(mix) == {"fader": True, "mute": False}
    c.model.set_inputs_stereo(2, True); c.apply()
    assert saved.link(c.model, 2) and not saved.link(c.model, 1)
    c.model.set_pair_stereo(1, True); c.apply()
    assert saved.mix_shape(c.model.mix_for_bus(2)) and not saved.mix_shape(mix)

    out = d.outputs[1]
    d.set(out.volume, [100, 100]); d.set_item(out.source_l, "Mix A"); d.set(d.master_switch, [0])
    mon.set_state(4, BLUE); mon.set_state(8, RED); mon.set_pad(True)
    assert saved.any(mon)

    assert saved.revert(c, mon) == []
    assert not saved.any(mon) and saved.monitor(mon) == [False] * 10
    assert {e.numid: d.get(e) for e in d.elements if e.writable} == before
    assert not c.model.stereo_inputs and not c.model.mix_for_bus(2).stereo


def test_saved_record_knows_its_connection():
    from scarlettmix.saved import Saved
    b = FakeBackend(extras=True); d = Device(b)
    record = presets.saved_record(d, MixController(d).model)
    assert record["connection"] == b.connection_id()
    b.power_cycle()
    assert record["connection"] != b.connection_id()     # it has been restarted since
    assert Saved(d, record).known
