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


def output_channels(label: str) -> tuple[str, str]:
    """Names of the two channels of an output pair, e.g. 'Line 7/8'."""
    if m := re.fullmatch(r"(.*?) ?(\d+)/(\d+)", label):
        kind = m.group(1) or "Line"
        return f"{kind} Output {m.group(2)}", f"{kind} Output {m.group(3)}"
    return f"{label} Output 1", f"{label} Output 2"


def output_pair(label: str, usb_id: str = "") -> str:
    """Name of an output pair for menus, e.g. 'Line 7/8 (Headphones 1)'."""
    hp = HEADPHONES.get(usb_id, {}).get(label)
    text = label if re.search(r"\d", label) else f"{label} 1/2"
    return f"{text} (Headphones {hp})" if hp else text
