"""Minimal ctypes binding to the ALSA control API (libasound).

Only what the mixer needs: enumerate a card's control elements, read and
write them, and get notified when something else changes them.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from ctypes import POINTER, byref, c_char_p, c_int, c_long, c_short, c_uint, c_void_p
from dataclasses import dataclass, field

# snd_ctl_elem_type_t
TYPE_BOOLEAN = 1
TYPE_INTEGER = 2
TYPE_ENUMERATED = 3

SND_CTL_NONBLOCK = 0x0001
SND_CTL_EVENT_MASK_VALUE = 1 << 0
SND_CTL_EVENT_MASK_REMOVE = 0xFFFFFFFF

_lib = None


class AlsaError(OSError):
    pass


class _PollFd(ctypes.Structure):
    _fields_ = [("fd", c_int), ("events", c_short), ("revents", c_short)]


def _load():
    global _lib
    if _lib is not None:
        return _lib
    name = ctypes.util.find_library("asound") or "libasound.so.2"
    lib = ctypes.CDLL(name, use_errno=True)

    def sig(fn, restype, *argtypes):
        f = getattr(lib, fn)
        f.restype = restype
        f.argtypes = list(argtypes)

    P = c_void_p
    sig("snd_strerror", c_char_p, c_int)
    sig("snd_ctl_open", c_int, POINTER(P), c_char_p, c_int)
    sig("snd_ctl_close", c_int, P)
    sig("snd_ctl_subscribe_events", c_int, P, c_int)
    sig("snd_ctl_poll_descriptors_count", c_int, P)
    sig("snd_ctl_poll_descriptors", c_int, P, POINTER(_PollFd), c_uint)
    sig("snd_ctl_read", c_int, P, P)

    for kind in ("elem_list", "elem_id", "elem_info", "elem_value", "event"):
        sig(f"snd_ctl_{kind}_malloc", c_int, POINTER(P))
        sig(f"snd_ctl_{kind}_free", None, P)

    sig("snd_ctl_elem_list", c_int, P, P)
    sig("snd_ctl_elem_list_get_count", c_uint, P)
    sig("snd_ctl_elem_list_get_used", c_uint, P)
    sig("snd_ctl_elem_list_alloc_space", c_int, P, c_uint)
    sig("snd_ctl_elem_list_free_space", None, P)
    sig("snd_ctl_elem_list_get_numid", c_uint, P, c_uint)
    sig("snd_ctl_elem_list_get_name", c_char_p, P, c_uint)
    sig("snd_ctl_elem_list_get_index", c_uint, P, c_uint)

    sig("snd_ctl_elem_info", c_int, P, P)
    sig("snd_ctl_elem_info_set_numid", None, P, c_uint)
    sig("snd_ctl_elem_info_get_type", c_int, P)
    sig("snd_ctl_elem_info_get_count", c_uint, P)
    sig("snd_ctl_elem_info_is_readable", c_int, P)
    sig("snd_ctl_elem_info_is_writable", c_int, P)
    sig("snd_ctl_elem_info_get_min", c_long, P)
    sig("snd_ctl_elem_info_get_max", c_long, P)
    sig("snd_ctl_elem_info_get_items", c_uint, P)
    sig("snd_ctl_elem_info_set_item", None, P, c_uint)
    sig("snd_ctl_elem_info_get_item_name", c_char_p, P)

    sig("snd_ctl_elem_read", c_int, P, P)
    sig("snd_ctl_elem_write", c_int, P, P)
    sig("snd_ctl_elem_value_set_numid", None, P, c_uint)
    sig("snd_ctl_elem_value_get_boolean", c_int, P, c_uint)
    sig("snd_ctl_elem_value_set_boolean", None, P, c_uint, c_long)
    sig("snd_ctl_elem_value_get_integer", c_long, P, c_uint)
    sig("snd_ctl_elem_value_set_integer", None, P, c_uint, c_long)
    sig("snd_ctl_elem_value_get_enumerated", c_uint, P, c_uint)
    sig("snd_ctl_elem_value_set_enumerated", None, P, c_uint, c_uint)

    sig("snd_ctl_event_elem_get_mask", c_uint, P)
    sig("snd_ctl_event_elem_get_numid", c_uint, P)
    _lib = lib
    return lib


def _check(ret: int, what: str) -> int:
    if ret < 0:
        msg = _load().snd_strerror(ret).decode(errors="replace")
        raise AlsaError(-ret, f"{what}: {msg}")
    return ret


@dataclass
class ElemInfo:
    """Static description of one control element."""

    numid: int
    name: str
    index: int
    type: int
    count: int
    readable: bool
    writable: bool
    min: int = 0
    max: int = 0
    items: list[str] = field(default_factory=list)


class Ctl:
    """An open ALSA control device (``hw:N``)."""

    def __init__(self, card: int):
        self._lib = _load()
        self._h = c_void_p()
        _check(self._lib.snd_ctl_open(byref(self._h), f"hw:{card}".encode(), SND_CTL_NONBLOCK),
               f"open hw:{card}")
        self.card = card

    def close(self) -> None:
        if self._h:
            self._lib.snd_ctl_close(self._h)
            self._h = c_void_p()

    def _alloc(self, kind: str) -> c_void_p:
        p = c_void_p()
        _check(getattr(self._lib, f"snd_ctl_{kind}_malloc")(byref(p)), f"{kind} malloc")
        return p

    def elements(self) -> list[ElemInfo]:
        lib = self._lib
        lst = self._alloc("elem_list")
        info = self._alloc("elem_info")
        try:
            _check(lib.snd_ctl_elem_list(self._h, lst), "elem_list")
            count = lib.snd_ctl_elem_list_get_count(lst)
            _check(lib.snd_ctl_elem_list_alloc_space(lst, count), "elem_list alloc")
            _check(lib.snd_ctl_elem_list(self._h, lst), "elem_list")
            out = []
            for i in range(lib.snd_ctl_elem_list_get_used(lst)):
                numid = lib.snd_ctl_elem_list_get_numid(lst, i)
                name = lib.snd_ctl_elem_list_get_name(lst, i).decode(errors="replace")
                index = lib.snd_ctl_elem_list_get_index(lst, i)
                lib.snd_ctl_elem_info_set_numid(info, numid)
                _check(lib.snd_ctl_elem_info(self._h, info), f"elem_info {name}")
                e = ElemInfo(
                    numid=numid, name=name, index=index,
                    type=lib.snd_ctl_elem_info_get_type(info),
                    count=lib.snd_ctl_elem_info_get_count(info),
                    readable=bool(lib.snd_ctl_elem_info_is_readable(info)),
                    writable=bool(lib.snd_ctl_elem_info_is_writable(info)),
                )
                if e.type == TYPE_INTEGER:
                    e.min = lib.snd_ctl_elem_info_get_min(info)
                    e.max = lib.snd_ctl_elem_info_get_max(info)
                elif e.type == TYPE_ENUMERATED:
                    for item in range(lib.snd_ctl_elem_info_get_items(info)):
                        lib.snd_ctl_elem_info_set_item(info, item)
                        _check(lib.snd_ctl_elem_info(self._h, info), f"elem_info item {name}")
                        e.items.append(lib.snd_ctl_elem_info_get_item_name(info).decode(errors="replace"))
                out.append(e)
            return out
        finally:
            lib.snd_ctl_elem_list_free_space(lst)
            lib.snd_ctl_elem_list_free(lst)
            lib.snd_ctl_elem_info_free(info)

    def read(self, e: ElemInfo) -> list[int]:
        lib = self._lib
        val = self._alloc("elem_value")
        try:
            lib.snd_ctl_elem_value_set_numid(val, e.numid)
            _check(lib.snd_ctl_elem_read(self._h, val), f"read {e.name}")
            if e.type == TYPE_BOOLEAN:
                return [lib.snd_ctl_elem_value_get_boolean(val, i) for i in range(e.count)]
            if e.type == TYPE_ENUMERATED:
                return [lib.snd_ctl_elem_value_get_enumerated(val, i) for i in range(e.count)]
            return [lib.snd_ctl_elem_value_get_integer(val, i) for i in range(e.count)]
        finally:
            lib.snd_ctl_elem_value_free(val)

    def write(self, e: ElemInfo, values: list[int]) -> None:
        lib = self._lib
        val = self._alloc("elem_value")
        try:
            lib.snd_ctl_elem_value_set_numid(val, e.numid)
            for i, v in enumerate(values[: e.count]):
                if e.type == TYPE_BOOLEAN:
                    lib.snd_ctl_elem_value_set_boolean(val, i, int(bool(v)))
                elif e.type == TYPE_ENUMERATED:
                    lib.snd_ctl_elem_value_set_enumerated(val, i, int(v))
                else:
                    lib.snd_ctl_elem_value_set_integer(val, i, int(v))
            _check(lib.snd_ctl_elem_write(self._h, val), f"write {e.name}")
        finally:
            lib.snd_ctl_elem_value_free(val)

    def subscribe(self) -> int:
        """Enable change events and return a file descriptor to poll for them."""
        _check(self._lib.snd_ctl_subscribe_events(self._h, 1), "subscribe")
        n = self._lib.snd_ctl_poll_descriptors_count(self._h)
        fds = (_PollFd * max(n, 1))()
        _check(self._lib.snd_ctl_poll_descriptors(self._h, fds, n), "poll descriptors")
        return fds[0].fd

    def drain_events(self) -> tuple[set[int], bool]:
        """Return (numids whose value changed, whether the card went away)."""
        lib = self._lib
        ev = self._alloc("event")
        changed: set[int] = set()
        gone = False
        try:
            while True:
                r = lib.snd_ctl_read(self._h, ev)
                if r == 0 or r == -11:  # nothing pending / EAGAIN
                    break
                if r < 0:
                    gone = True
                    break
                mask = lib.snd_ctl_event_elem_get_mask(ev)
                if mask == SND_CTL_EVENT_MASK_REMOVE:
                    gone = True
                elif mask & SND_CTL_EVENT_MASK_VALUE:
                    changed.add(lib.snd_ctl_event_elem_get_numid(ev))
        finally:
            lib.snd_ctl_event_free(ev)
        return changed, gone


def list_cards() -> list[tuple[int, str, str]]:
    """Return (card number, usb id 'vvvv:pppp' or '', long name) for every sound card."""
    out = []
    base = "/proc/asound"
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return out
    for entry in entries:
        if not (entry.startswith("card") and entry[4:].isdigit()):
            continue
        n = int(entry[4:])
        usbid = _read(f"{base}/{entry}/usbid")
        out.append((n, usbid, _card_name(n)))
    return out


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _card_name(n: int) -> str:
    for line in _read("/proc/asound/cards").splitlines():
        parts = line.split("]: ", 1)
        if len(parts) == 2 and line.strip().split(" ", 1)[0] == str(n):
            return parts[1].split(" - ", 1)[-1].strip()
    return f"card {n}"
