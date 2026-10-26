"""Sample rate and buffer size, which on Linux belong to the audio server.

PipeWire runs one clock for the whole graph. Its ``settings`` metadata (what
``pw-metadata -n settings 0`` shows) holds the defaults it started with and
two overrides, ``clock.force-rate`` and ``clock.force-quantum``; 0 means no
override. Setting those changes the clock at once and lasts until PipeWire
restarts. To keep a choice across restarts, ScarlettMix writes it as
PipeWire's defaults in a drop-in file of its own; it never edits other files.
"""

from __future__ import annotations

import os
import re
import subprocess

RATES = (44100, 48000, 88200, 96000)
QUANTA = (32, 64, 128, 256, 512, 1024, 2048)
CONF_NAME = "90-scarlettmix-clock.conf"


class AudioServerError(Exception):
    pass


def conf_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "pipewire", "pipewire.conf.d", CONF_NAME)


def parse_metadata(text: str) -> dict[str, str]:
    """The id-0 entries of ``pw-metadata`` output, as key -> value."""
    return {m.group(1): m.group(2)
            for m in re.finditer(r"update: id:0 key:'([^']*)' value:'([^']*)'", text)}


def _int(values: dict[str, str], key: str) -> int:
    try:
        return int(values.get(key, "0"))
    except ValueError:
        return 0


def _ints(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", text)]


class ClockState:
    """One reading of the graph clock."""

    def __init__(self, values: dict[str, str]):
        self.force_rate = _int(values, "clock.force-rate")
        self.force_quantum = _int(values, "clock.force-quantum")
        self.rate = _int(values, "clock.rate")
        self.allowed_rates = _ints(values.get("clock.allowed-rates", ""))
        self.quantum = _int(values, "clock.quantum")
        self.min_quantum = _int(values, "clock.min-quantum")
        self.max_quantum = _int(values, "clock.max-quantum")

    def default_rate(self) -> int:
        """The rate PipeWire runs at without an override, or 0 if it follows
        the applications (more than one allowed rate)."""
        if len(set(self.allowed_rates)) > 1:
            return 0
        return self.allowed_rates[0] if self.allowed_rates else self.rate

    def default_quantum(self) -> int:
        """The buffer size without an override, or 0 if it follows the
        applications (a range of sizes is allowed)."""
        return self.min_quantum if self.min_quantum and self.min_quantum == self.max_quantum else 0

    def running_rate(self) -> int:
        return self.force_rate or self.default_rate() or self.rate


def read_conf(path: str | None = None) -> tuple[int, int]:
    """(rate, quantum) kept in our drop-in file; 0 where it keeps none."""
    try:
        with open(path or conf_path()) as f:
            text = f.read()
    except OSError:
        return 0, 0
    rate = re.search(r"default\.clock\.rate\s*=\s*(\d+)", text)
    quantum = re.search(r"default\.clock\.quantum\s*=\s*(\d+)", text)
    return int(rate.group(1)) if rate else 0, int(quantum.group(1)) if quantum else 0


def write_conf(rate: int, quantum: int, path: str | None = None) -> None:
    """Keep (rate, quantum) for the next PipeWire start; 0 keeps none. With
    neither, the file is removed."""
    path = path or conf_path()
    if not rate and not quantum:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    lines = ["# Written by ScarlettMix (Rate and Buffer in its header). Delete this file",
             "# and restart PipeWire to go back to the defaults.",
             "context.properties = {"]
    if rate:
        lines += [f"    default.clock.rate          = {rate}",
                  f"    default.clock.allowed-rates = [ {rate} ]"]
    if quantum:
        lines += [f"    default.clock.quantum       = {quantum}",
                  f"    default.clock.min-quantum   = {quantum}",
                  f"    default.clock.max-quantum   = {quantum}"]
    lines.append("}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


class PipeWireClock:
    """The running PipeWire's clock, through ``pw-metadata``."""

    simulated = False

    def _run(self, *args: str) -> str:
        try:
            proc = subprocess.run(["pw-metadata", "-n", "settings", "0", *args],
                                  capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError) as err:
            raise AudioServerError(f"cannot reach PipeWire: {err}") from err
        if proc.returncode != 0:
            raise AudioServerError(f"cannot reach PipeWire: {proc.stderr.strip() or proc.returncode}")
        return proc.stdout

    def read(self) -> ClockState:
        values = parse_metadata(self._run())
        if "clock.rate" not in values:
            raise AudioServerError("PipeWire's settings are not available")
        return ClockState(values)

    def set_rate(self, rate: int) -> None:
        self._run("clock.force-rate", str(rate))

    def set_quantum(self, quantum: int) -> None:
        self._run("clock.force-quantum", str(quantum))

    def kept(self) -> tuple[int, int]:
        return read_conf()

    def keep(self, rate: int, quantum: int) -> None:
        write_conf(rate, quantum)


class FakeClock:
    """A PipeWire clock in memory, for the simulated device and tests."""

    simulated = True

    def __init__(self):
        self.values = {"clock.rate": "48000", "clock.allowed-rates": "[ 48000 ]",
                       "clock.quantum": "1024", "clock.min-quantum": "32",
                       "clock.max-quantum": "2048", "clock.force-rate": "0",
                       "clock.force-quantum": "0"}
        self.writes: list[tuple[str, int]] = []
        self._kept = (0, 0)

    def read(self) -> ClockState:
        return ClockState(self.values)

    def set_rate(self, rate: int) -> None:
        self.values["clock.force-rate"] = str(rate)
        self.writes.append(("clock.force-rate", rate))

    def set_quantum(self, quantum: int) -> None:
        self.values["clock.force-quantum"] = str(quantum)
        self.writes.append(("clock.force-quantum", quantum))

    def kept(self) -> tuple[int, int]:
        return self._kept

    def keep(self, rate: int, quantum: int) -> None:
        self._kept = (rate, quantum)
