#!/bin/bash
# Swap the running USB audio driver for the test build. Nothing is installed:
# a reboot, or ./revert.sh, puts the stock driver back.
set -euo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
# Where build.sh keeps the kernel source and the built module.
BUILD="${SCARLETT_BUILD_DIR:-$HERE/../../kernel}"
cd "$BUILD" 2>/dev/null || { echo "no build directory at $BUILD; run driver/build.sh first"; exit 1; }
KO=sound/usb/snd-usb-audio.ko

[ -f "$KO" ] || { echo "test module not built: $KO"; exit 1; }
[ "$(modinfo -F vermagic "$KO" | cut -d' ' -f1)" = "$(uname -r)" ] || {
    echo "test module was built for another kernel than $(uname -r); rebuild first"; exit 1; }

echo "Stopping the ScarlettMix service and PipeWire (audio stops for a few seconds)..."
systemctl --user stop scarlettmix.service 2>/dev/null || true
systemctl --user stop pipewire.socket pipewire-pulse.socket 2>/dev/null || true
systemctl --user stop wireplumber.service pipewire-pulse.service pipewire.service 2>/dev/null || true
sleep 1

restart_audio() {
    systemctl --user start pipewire.socket pipewire-pulse.socket 2>/dev/null || true
    systemctl --user start pipewire.service wireplumber.service pipewire-pulse.service 2>/dev/null || true
}

# One sudo call. The udev override lives in /run, so it disappears on reboot;
# it stops alsactl from rewriting the mixer when the interface reappears,
# which would hide what the interface itself remembered.
if ! sudo sh -c '
    set -e
    rmmod snd_usb_audio
    mkdir -p /run/udev/rules.d
    : > /run/udev/rules.d/90-alsa-restore.rules
    udevadm control --reload
    insmod "$1"
' sh "$PWD/$KO"; then
    echo
    echo "Could not swap the driver. If it said the module is in use, close ScarlettMix"
    echo "and any program using the interface, then run this again."
    lsmod | grep -q '^snd_usb_audio' || sudo modprobe snd_usb_audio
    restart_audio
    exit 1
fi

restart_audio
sleep 2
loaded=$(cat /sys/module/snd_usb_audio/srcversion)
if [ "$loaded" = "$(modinfo -F srcversion "$KO")" ]; then
    echo "TEST driver is loaded (srcversion $loaded)."
else
    echo "Unexpected: loaded driver srcversion $loaded is not the test build."
fi
systemctl --user start scarlettmix.service 2>/dev/null || true
echo "ScarlettMix service: $(systemctl --user is-active scarlettmix.service). Run ./revert.sh to go back to the stock driver."
