"""Names as the original MixControl shows them.

The kernel driver calls things "PCM 3", "Mix A" and "Line 7/8"; the original
says "DAW 3", "Mix 1" and "Line Output 7". The interface speaks driver names;
the screen shows these.
"""

from __future__ import annotations

import re

# Analogue pairs that also feed a headphone socket, from the original's
# routing panel artwork (headphone icons beside Line Outputs 7 and 9).
HEADPHONES = {"1235:800c": {"Line 7/8": 1, "Line 9/10": 2}}


def source(item: str) -> str:
    """Display name for an entry in a source selector."""
    if m := re.fullmatch(r"PCM (\d+)", item):
        return f"DAW {m.group(1)}"
    if m := re.fullmatch(r"Mix ([A-Z])", item):
        return f"Mix {ord(m.group(1)) - ord('A') + 1}"
    return item


def mix(buses: list[int]) -> str:
    return "Mix " + "/".join(str(b + 1) for b in buses)


def output_title(label: str, usb_id: str = "") -> tuple[str, str]:
    """What an output pair is, and which sockets it is: ("Headphones 1",
    "Line out 7/8"), ("Main monitors", "Line out 1/2"), ("S/PDIF out", "coaxial")."""
    hp = HEADPHONES.get(usb_id, {}).get(label)
    numbers = label.split(" ", 1)[1] if " " in label else ""
    if label == "Monitor":
        return "Main monitors", "Line out 1/2"
    if hp:
        return f"Headphones {hp}", f"Line out {numbers}"
    if label.startswith("Line"):
        return f"Line out {numbers}", ""
    if label == "SPDIF":
        return "S/PDIF out", "coaxial"
    if label.startswith("ADAT"):
        return f"ADAT out {numbers}", "optical"
    return label, ""


def output_channels(label: str) -> tuple[str, str]:
    """Names of the two channels of an output pair, e.g. 'Line 7/8'."""
    if label == "SPDIF":
        return "S/PDIF out L", "S/PDIF out R"
    if label == "Monitor":
        return "Line out 1", "Line out 2"
    if m := re.fullmatch(r"(.*?) ?(\d+)/(\d+)", label):
        kind = m.group(1) or "Line"
        return f"{kind} out {m.group(2)}", f"{kind} out {m.group(3)}"
    return f"{label} L", f"{label} R"


def output_pair(label: str, usb_id: str = "") -> str:
    """Name of an output pair for menus, e.g. 'Headphones 1 (Line out 7/8)'."""
    name, socket = output_title(label, usb_id)
    return f"{name} ({socket})" if socket and not socket.islower() else name


# -- sources, as offered by the routing selectors ------------------------------

def channel(item: str) -> str:
    """One source channel in plain words: "DAW 3", "Mix 2", "Analog in 5",
    "S/PDIF in L", "ADAT in 4", "Off"."""
    if m := re.fullmatch(r"Analog (\d+)", item):
        return f"Analog in {m.group(1)}"
    if m := re.fullmatch(r"SPDIF (\d+)", item):
        return "S/PDIF in " + ("L" if m.group(1) == "1" else "R")
    if m := re.fullmatch(r"ADAT (\d+)", item):
        return f"ADAT in {m.group(1)}"
    return source(item)


# (heading, driver item pattern, how a pair is shown)
_PAIR_GROUPS = (
    ("From the computer", r"PCM (\d+)", lambda a, b: f"DAW {a}/{b}"),
    ("Mixes", r"Mix ([A-Z])", None),
    ("Inputs", r"Analog (\d+)", lambda a, b: f"Analog in {a}/{b}"),
    ("Inputs", r"SPDIF (\d+)", lambda a, b: "S/PDIF in"),
    ("Inputs", r"ADAT (\d+)", lambda a, b: f"ADAT in {a}/{b}"),
)


def source_pairs(items: list[str]) -> list[tuple[str, str, tuple[str, str]]]:
    """Stereo sources a selector offers, in groups: (heading, shown name,
    (left item, right item)). "Off" comes first with an empty heading."""
    out = [("", "Off", ("Off", "Off"))] if "Off" in items else []
    for heading, pattern, show in _PAIR_GROUPS:
        found = [(re.fullmatch(pattern, i).group(1), i) for i in items if re.fullmatch(pattern, i)]
        for (a, left), (b, right) in zip(found[0::2], found[1::2]):
            if show is None:                      # mixes: letters, shown as numbers
                na, nb = ord(a) - ord("A") + 1, ord(b) - ord("A") + 1
                name = f"Mix {na}/{nb}"
            else:
                name = show(a, b)
            out.append((heading, name, (left, right)))
    return out


def pair_name(left: str, right: str, items: list[str]) -> tuple[str, bool]:
    """How a left/right choice is shown, and whether it is one of the
    offered stereo pairs."""
    for _, name, pair in source_pairs(items):
        if pair == (left, right):
            return name, True
    return f"{channel(left)} + {channel(right)}", False
