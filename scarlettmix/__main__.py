"""Command-line entry point: ``python -m scarlettmix``."""

from __future__ import annotations

import argparse
import sys

from . import presets
from .alsa import AlsaError
from .backend import SUPPORTED_USB_IDS, AlsaBackend, FakeBackend, find_cards
from .device import Device, level_to_db
from .mixer import MixController


def open_backend(args):
    if args.demo:
        return FakeBackend(extras=True)
    if args.card is not None:
        return AlsaBackend(args.card)
    cards = find_cards()
    if not cards:
        return None
    return AlsaBackend(cards[0][0])


def print_status(device: Device) -> None:
    print(f"{device.model_name} [{device.backend.usb_id}] - {device.backend.name}")
    if device.clock_source:
        sync = device.item(device.sync_status) if device.sync_status else "?"
        rate = device.backend.sample_rate()
        print(f"clock: {device.item(device.clock_source)} ({sync}), "
              f"rate: {rate if rate else 'idle'}")
    print("\noutputs:")
    for o in device.outputs:
        srcs = " / ".join(device.item(e) for e in o.sources)
        vol = "/".join(f"{level_to_db(v):+d}" for v in device.get(o.volume)) if o.volume else "-"
        muted = " muted" if o.switch and not any(device.get(o.switch)) else ""
        print(f"  {o.label:<10} <- {srcs:<20} {vol} dB{muted}")
    print("\nDAW inputs:")
    print("  " + ", ".join(f"{i + 1}:{device.item(e)}" for i, e in enumerate(device.capture)))
    print("\nmixer inputs:")
    print("  " + ", ".join(f"{i + 1}:{device.item(e)}" for i, e in enumerate(device.matrix_inputs)))
    print("\nmix levels (dB, '.' = off):")
    levels = device.matrix_levels()
    print("  in  " + "".join(f"{n:>5}" for n in device.mix_names))
    for i in range(device.n_inputs):
        cells = "".join(f"{'.' if levels[(i, b)] == 0 else level_to_db(levels[(i, b)]):>5}"
                        for b in range(device.n_mixes))
        print(f"  {i + 1:>2}  {cells}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scarlettmix",
                                 description="Mixer and routing control for first-generation "
                                             "Focusrite Scarlett interfaces.")
    ap.add_argument("--card", type=int, help="ALSA card number (default: first supported Scarlett)")
    ap.add_argument("--demo", action="store_true", help="run against a simulated 18i20")
    ap.add_argument("--status", action="store_true", help="print the device's current setup and exit")
    ap.add_argument("--load", metavar="FILE", help="apply a preset (or MixControl snapshot) and exit")
    ap.add_argument("--save", metavar="FILE", help="write the device's current setup to a preset and exit")
    ap.add_argument("--daemon", action="store_true",
                    help="run in the background: remember every change and restore the "
                         "setup whenever the interface appears")
    ap.add_argument("--restore", action="store_true",
                    help="reapply the remembered setup once and exit")
    args = ap.parse_args(argv)

    if args.daemon:
        from . import daemon
        return daemon.run()

    try:
        backend = open_backend(args)
    except AlsaError as err:
        print(f"scarlettmix: {err}", file=sys.stderr)
        return 1

    if args.status or args.load or args.save or args.restore:
        if backend is None:
            print("scarlettmix: no supported Scarlett found "
                  f"({', '.join(SUPPORTED_USB_IDS.values())})", file=sys.stderr)
            return 1
        device = Device(backend)
        if args.load:
            controller = MixController(device)
            preset, notes = presets.load_file(args.load, device)
            notes += presets.apply(preset, device, controller)
            if not backend.simulated:
                presets.save_state(device, controller.model)
            for n in notes:
                print(f"note: {n}", file=sys.stderr)
        if args.restore:
            notes = presets.restore_last(device, force=True)
            if notes is None:
                print("scarlettmix: no saved session to restore", file=sys.stderr)
            for n in notes or []:
                print(f"note: {n}", file=sys.stderr)
        if args.save:
            saved = None if backend.simulated else presets.load_state(device)
            presets.save_file(args.save, presets.snapshot(device, MixController(device, saved).model))
        if args.status:
            print_status(device)
        backend.close()
        return 0

    from PySide6.QtWidgets import QApplication, QMessageBox

    from .ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("ScarlettMix")
    app.setDesktopFileName("scarlettmix")
    if backend is None:
        box = QMessageBox(QMessageBox.Warning, "ScarlettMix",
                          "No supported Scarlett interface was found.")
        box.setInformativeText("Supported: " + ", ".join(SUPPORTED_USB_IDS.values())
                               + " (first generation).\n\nOpen a simulated 18i20 instead?")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Close)
        if box.exec() != QMessageBox.Yes:
            return 1
        backend = FakeBackend()
    win = MainWindow(backend)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
