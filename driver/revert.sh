#!/bin/bash
# Put the stock USB audio driver back and undo everything load-test.sh did.
set -euo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
# Where build.sh keeps the kernel source and the built module.
BUILD="${SCARLETT_BUILD_DIR:-$HERE/../../kernel}"
cd "$BUILD" 2>/dev/null || { echo "no build directory at $BUILD; run driver/build.sh first"; exit 1; }

echo "Stopping PipeWire (audio stops for a few seconds)..."
systemctl --user stop scarlettmix.service 2>/dev/null || true
systemctl --user stop pipewire.socket pipewire-pulse.socket 2>/dev/null || true
systemctl --user stop wireplumber.service pipewire-pulse.service pipewire.service 2>/dev/null || true
sleep 1

sudo sh -c '
    rmmod snd_usb_audio 2>/dev/null || true
    rm -f /run/udev/rules.d/90-alsa-restore.rules
    udevadm control --reload
    modprobe snd_usb_audio
' || echo "Driver swap reported a problem; a reboot will also restore the stock driver."

systemctl --user start pipewire.socket pipewire-pulse.socket 2>/dev/null || true
systemctl --user start pipewire.service wireplumber.service pipewire-pulse.service 2>/dev/null || true
sleep 2
systemctl --user start scarlettmix.service 2>/dev/null || true

loaded=$(cat /sys/module/snd_usb_audio/srcversion 2>/dev/null || echo none)
if [ "$loaded" = "$(modinfo -F srcversion snd-usb-audio)" ]; then
    echo "STOCK driver is loaded (srcversion $loaded)."
else
    echo "Loaded driver srcversion $loaded does not match the stock one; reboot to be sure."
fi
echo "ScarlettMix service: $(systemctl --user is-active scarlettmix.service)"
