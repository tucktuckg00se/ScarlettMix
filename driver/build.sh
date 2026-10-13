#!/bin/bash
# Build the patched USB audio driver for the running kernel. Nothing is
# installed: load-test.sh swaps it in until the next reboot or revert.sh.
set -euo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
BUILD="${SCARLETT_BUILD_DIR:-$HERE/../../kernel}"
KREL="$(uname -r)"
KVER="${KREL%%-*}"                       # e.g. 7.2.7 from 7.2.7-arch1-1
HEADERS="/usr/lib/modules/$KREL/build"

[ -d "$HEADERS" ] || { echo "kernel headers for $KREL are not installed"; exit 1; }
mkdir -p "$BUILD"
cd "$BUILD"

if [ "$(cat .kernel-version 2>/dev/null)" != "$KVER" ]; then
    echo "Fetching sound/usb from Linux $KVER..."
    rm -rf sound usb-orig
    curl -fsS "https://cdn.kernel.org/pub/linux/kernel/v${KVER%%.*}.x/linux-$KVER.tar.xz" \
        | tar -xJ --strip-components=1 "linux-$KVER/sound/usb"
    cp -r sound/usb usb-orig
    patch -p1 < "$HERE/scarlett-gen1-extras.patch"
    echo "$KVER" > .kernel-version
elif ! cmp -s <(diff -u usb-orig/mixer_scarlett.c sound/usb/mixer_scarlett.c | tail -n +3) \
              <(tail -n +3 "$HERE/scarlett-gen1-extras.patch"); then
    echo "Re-applying the current patch..."
    cp usb-orig/mixer_scarlett.c sound/usb/mixer_scarlett.c
    patch -p1 < "$HERE/scarlett-gen1-extras.patch"
fi

make -j"$(nproc)" -C "$HEADERS" M="$BUILD/sound/usb" modules
echo "Built $BUILD/sound/usb/snd-usb-audio.ko for $KREL. Load it with driver/load-test.sh."
