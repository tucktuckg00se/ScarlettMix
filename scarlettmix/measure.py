"""Measure the mixer gains the interface is really using, with its own meters.

After a power cycle the interface reports every mixer gain as 0 dB until it
has been sent again, while it is really running its saved mix. Its level
meters, however, are accurate. So a test tone is played on a DAW channel
that nothing else uses, routed into one mixer input at a time (all other
mixer inputs off), and the eight mix meters give that input's gain into
every mix. Outputs that play the mixes are muted meanwhile. Routing and
mutes are read first and put back exactly. Takes about ten seconds.

Output levels cannot be measured this way: outputs have no meters.
"""

from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import tempfile
import time
import wave
from typing import Callable

from .device import Device, db_to_level

TONE_DBFS = -12.0
OFF_BELOW_DB = -70.0        # quieter than this cannot be told from off


class MeasureError(Exception):
    pass


def free_channel(device: Device) -> int | None:
    """Index of a DAW playback channel that no output or mixer input uses."""
    used = {device.item(e) for o in device.outputs for e in o.sources}
    used |= {device.item(e) for e in device.matrix_inputs}
    items = device.matrix_inputs[0].items if device.matrix_inputs else []
    for n in range(len(items), 0, -1):
        name = f"PCM {n}"
        if name in items and name not in used:
            return n - 1
    return None


def measure_gains(device: Device, play: Callable[[int], Callable[[], None]],
                  sleep: Callable[[float], None] = time.sleep) -> dict[tuple[int, int], int]:
    """Measure every mixer gain. ``play(channel)`` starts the test tone on
    that DAW channel (0-based) and returns a function that stops it. Returns
    {(input, bus): driver level (0 = off)}."""
    if not device.has_meters:
        raise MeasureError("the level meters are needed (patched driver)")
    channel = free_channel(device)
    if channel is None:
        raise MeasureError("no DAW channel is free for the test tone")
    test = f"PCM {channel + 1}"
    b = device.backend
    inputs = device.matrix_inputs
    routes = [device.item(e) for e in inputs]
    muted = [o for o in device.outputs
             if o.switch and any(device.item(s).startswith("Mix") for s in o.sources)]
    switches = {o.number: b.read(o.switch) for o in muted}
    stop = None
    result: dict[tuple[int, int], int] = {}
    try:
        for o in muted:
            b.write(o.switch, [0] * o.switch.count)
        for e in inputs:
            device.set_item(e, "Off")
        stop = play(channel)
        sleep(1.2)
        b.read(device.meter_pcm)
        sleep(0.3)
        if b.read(device.meter_pcm)[channel] < 1000:
            raise MeasureError("the test tone does not reach the interface (the audio "
                               "server needs the interface's Pro Audio profile)")
        for i, e in enumerate(inputs):
            device.set_item(e, test)
            sleep(0.25)
            b.read(device.meter_mixes)
            b.read(device.meter_pcm)
            sleep(0.35)
            mixes = b.read(device.meter_mixes)
            ref = b.read(device.meter_pcm)[channel]
            device.set_item(e, "Off")
            for bus, peak in enumerate(mixes):
                cell = device.matrix_gain[(i, bus)]
                db = 20 * math.log10(peak / ref) if peak and ref else -math.inf
                result[(i, bus)] = 0 if db < OFF_BELOW_DB else db_to_level(round(db), cell)
    finally:
        if stop:
            stop()
        for e, item in zip(inputs, routes):
            device.set_item(e, item)
        for o in muted:
            b.write(o.switch, switches[o.number])
    return result


def simulated_player(backend) -> Callable[[int], Callable[[], None]]:
    """``play`` for the simulated interface: the tone exists only in its meters."""
    def play(channel: int) -> Callable[[], None]:
        backend.playing[f"PCM {channel + 1}"] = 10 ** (TONE_DBFS / 20)
        return backend.playing.clear
    return play


# -- playing the tone through PipeWire ---------------------------------------

def pipewire_player(card: int) -> Callable[[int], Callable[[], None]]:
    """A ``play`` function for measure_gains that plays through the PipeWire
    sink of this card's Pro Audio profile, using ``pw-play``."""
    try:
        nodes = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True,
                                          timeout=10).stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError) as err:
        raise MeasureError(f"cannot reach PipeWire: {err}") from err
    sink = channels = None
    for node in nodes:
        props = (node.get("info") or {}).get("props") or {}
        if (props.get("media.class") == "Audio/Sink" and str(props.get("alsa.card")) == str(card)
                and "pro-output" in str(props.get("node.name", ""))):
            sink, channels = props["node.name"], int(props.get("audio.channels", 0))
    if not sink:
        raise MeasureError("the interface's Pro Audio output is not available in PipeWire")

    def play(channel: int) -> Callable[[], None]:
        path = os.path.join(tempfile.mkdtemp(prefix="scarlettmix-"), "tone.wav")
        # 1 kHz at 48 kHz: one cycle is 48 frames, repeated for 30 seconds.
        rate, seconds, period = 48000, 30, 48
        amp = round(32767 * 10 ** (TONE_DBFS / 20))
        cycle = bytearray()
        for n in range(period):
            frame = [0] * channels
            frame[channel] = round(amp * math.sin(2 * math.pi * n / period))
            cycle += struct.pack(f"<{channels}h", *frame)
        with wave.open(path, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(cycle) * (rate * seconds // period))
        proc = subprocess.Popen(["pw-play", "--target", sink, path],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop() -> None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            finally:
                os.remove(path)
                os.rmdir(os.path.dirname(path))
        return stop

    return play
