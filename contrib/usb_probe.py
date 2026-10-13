#!/usr/bin/env python3
"""Read-only check of what raw USB access to the Scarlett allows.

Sends nothing that can change the interface: every request here is a read.
Run after installing contrib/70-scarlett-usb.rules.
"""

import ctypes
import errno
import fcntl
import glob
import os
import sys

USBDEVFS_CONTROL = 0xC0185500
IDS = {"8002", "8004", "800c", "8012", "8014"}
MIXER_UNIT = 0x3C
UAC2_MEM = 0x03


class CtrlTransfer(ctypes.Structure):
    _fields_ = [("bRequestType", ctypes.c_uint8), ("bRequest", ctypes.c_uint8),
                ("wValue", ctypes.c_uint16), ("wIndex", ctypes.c_uint16),
                ("wLength", ctypes.c_uint16), ("timeout", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]


def find_device():
    for d in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        base = os.path.dirname(d)
        read = lambda n: open(os.path.join(base, n)).read().strip()
        if read("idVendor") == "1235" and read("idProduct") in IDS:
            return f"/dev/bus/usb/{int(read('busnum')):03d}/{int(read('devnum')):03d}"
    return None


def mem_read(fd, register, length, interface):
    buf = ctypes.create_string_buffer(length)
    req = CtrlTransfer(0xA1, UAC2_MEM, register, (MIXER_UNIT << 8) | interface, length, 1000,
                       ctypes.cast(buf, ctypes.c_void_p))
    got = fcntl.ioctl(fd, USBDEVFS_CONTROL, req)
    return buf.raw[:got]


def main():
    path = find_device()
    if not path:
        sys.exit("no supported Scarlett found")
    print(f"device node: {path}")
    try:
        fd = os.open(path, os.O_RDWR)
    except PermissionError:
        sys.exit("no write access to the device node: install contrib/70-scarlett-usb.rules "
                 "(see the comment at its top), then replug or run the udevadm lines")
    print("write access: yes")

    try:
        data = mem_read(fd, 0, 36, interface=0)
        print(f"meter read via the audio control interface: {data.hex(' ')}")
        print("=> the kernel let it through; meters and status can be read live")
    except OSError as e:
        why = "interface owned by snd-usb-audio, as expected" if e.errno == errno.EBUSY else ""
        print(f"meter read via the audio control interface: refused ({errno.errorcode.get(e.errno, e.errno)}) {why}")

    # Do not address requests to any other interface to get around this. Doing
    # so with the firmware-update interface (5 on the 18i20) on 2026-10-13 made
    # the unit drop off the USB bus until it was power-cycled.
    os.close(fd)


if __name__ == "__main__":
    main()
