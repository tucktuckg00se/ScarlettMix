# Patched driver for first-generation Scarletts (test build)

`scarlett-gen1-extras.patch` adds controls to the kernel's
`sound/usb/mixer_scarlett.c` for the 18i20: level meters, front-panel status,
Save To HW, hardware Mute and Dim, and the monitor masks. What each does and
how it was measured is in `../docs/missing-features.md`.

    ./build.sh       # fetch matching kernel source, patch, build (no root)
    ./load-test.sh   # swap the driver in (sudo); audio stops for a few seconds
    ./revert.sh      # back to the stock driver

Nothing is installed. A reboot also returns to the stock driver, and the
module must be rebuilt after a kernel update. While the test driver is
loaded, `alsactl restore` is switched off for newly appearing cards (a file
under `/run`); `revert.sh` or a reboot undoes that.

The build directory defaults to `../../kernel` relative to this folder; set
`SCARLETT_BUILD_DIR` to use another.
