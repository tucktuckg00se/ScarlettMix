"""Gain curves recovered from Scarlett MixControl 1.10.

The original stores fader and pan positions as 0..1 floats and converts
them to decibels with an exponential curve; these functions reproduce it
so faders feel the same and original presets import at the right level.
"""

from __future__ import annotations

import math

# Hardware matrix / output gain range, in dB.
MIN_DB = -128.0
MAX_DB = 6.0

_FADER_BASE = 0.20192301273345947 ** 2
_BALANCE_BASE = 0.05263161659240723 ** 2
_BALANCE_MIN_DB = -134.0
_BALANCE_RANGE_DB = 133.99


def _curve(pos: float, base: float) -> float:
    """Map 0..1 to 0..1 along the original's exponential taper."""
    pos = min(1.0, max(0.0, pos))
    return (base ** pos - 1.0) / (base - 1.0)


def fader_to_db(pos: float) -> float:
    """Fader position (0..1) to dB; 0.775137 is unity."""
    return MIN_DB + (MAX_DB - MIN_DB) * _curve(pos, _FADER_BASE)


def db_to_fader(db: float) -> float:
    db = min(MAX_DB, max(MIN_DB, db))
    frac = (db - MIN_DB) / (MAX_DB - MIN_DB)
    return math.log(frac * (_FADER_BASE - 1.0) + 1.0) / math.log(_FADER_BASE)


def balance_db(x: float) -> float:
    """Attenuation for one side of a stereo bus; x >= 0.5 is unity."""
    if x >= 0.5:
        return 0.0
    return _BALANCE_MIN_DB + _BALANCE_RANGE_DB * _curve(2.0 * x, _BALANCE_BASE)


def pan_gains(pan: float) -> tuple[float, float]:
    """(left dB, right dB) offsets for a pan position, 0 = left, 1 = right."""
    return balance_db(1.0 - pan), balance_db(pan)
