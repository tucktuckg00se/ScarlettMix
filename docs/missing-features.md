# Missing features: what MixControl sends, and how to get it on Linux

ScarlettMix lacks level meters, save-to-hardware and the monitor-knob setup.
This note records exactly what the original sends for each, taken from
`Scarlett MixControl.exe` (see `mixcontrol-protocol.md` for method and
addressing), what the Linux kernel driver already knows, and the route to
adding them.

Status of the evidence: everything in "What MixControl sends" is read from
the Windows binary by static analysis. None of it has been sent to a device
from Linux yet, because an unprivileged user cannot (see "Delivery").

## Common form

All three features use the same request shape on the mixer unit:

| Field | Value |
|---|---|
| bmRequestType | `0xa1` to read, `0x21` to write (class, interface) |
| bRequest | `0x03` (UAC2 `MEM`) |
| wValue | `(selector << 8) | register` |
| wIndex | `(0x3c << 8) | control interface number` (0 on the 18i20) |

"Register" is the value the binary passes as the channel byte. They are
register numbers, not byte offsets: the binary writes 4 bytes each to
registers `0x0a`, `0x0b` and `0x0c`, which would overlap otherwise.

## Test results on a real 18i20 (2026-10-13)

Tested with a patched `snd-usb-audio` 7.2.7 loaded temporarily (patch:
`../kernel/scarlett-gen1-extras.patch`), firmware 2.7a. Stage A is read-only.

**Meters: confirmed.**
- Registers 0, 1 and 3 return 18, 8 and 20 little-endian 16-bit values:
  hardware inputs, mix buses, DAW playback channels, each in channel order.
- The value is linear peak amplitude over the full 16 bits:
  `dB = 20 * log10(value / 65535)`. Measured with a 997 Hz sine on DAW 20:
  0 dBFS gave 65535, -6 gave 32845, -20 gave 6553, -40 gave 655, -60 gave 65.
- Each read returns the peak since the previous read, then resets.
- With DAW 1/2 feeding Mix 1/2 at 0 dB, the mix meter matched the DAW meter
  sample for sample; unused mixes read 0.

**Front-panel status (register 0x04): confirmed readable, bytes named.**

| Byte | Meaning | Observed |
|---|---|---|
| 0 | monitor knob as attenuation in dB | 0 at full up, 127 at full down |
| 1 | knob position, raw | 255 at full up, 0 at full down |
| 2 | Mute button state | 0 / 1 |
| 3 | Dim button state | 0 / 1 |

Bytes 2 and 3 were confirmed by writing the Mute and Dim registers and
watching which byte changed.

**Save To HW (0xa5 to register 0x5a): confirmed.** Three power cycles with
`alsactl restore` and every other restorer disabled:

| Before power-off | After power-on |
|---|---|
| routing changed, not saved | unit came up with its older stored setup (176 controls differed) |
| setup restored, test routing value set, saved | routing came up as saved, including the test value |
| gains verified by meter, saved | a tone on DAW 3 reached Mix 1 at the saved 0 dB with nothing rewritten; a tone on DAW 9 (saved as off) reached no mix |

So the save covers output routing, mixer-input routing and mixer gains. A
later power cycle, measured through the output-3 loopback, showed it also
covers output levels (saved at -10 dB, measured -10 dB after power-on), the
three monitor masks (saved 3 / 11 / 7, read back the same) and mono.

That same power cycle confirmed the read-back problem below for output
levels and mutes: the driver read output 3 as 0 dB and muted while it
measured -10 dB and was audible.

**Hardware Mute and Dim, and their output masks: confirmed.** Measured with
line output 3 looped into another sound card's line input, a -20 dBFS tone
routed to it, and one output bit added to each mask in turn:

| Register | Meaning | Measurement |
|---|---|---|
| `0x05` | Mute on/off (same as the front-panel button) | status byte 2 follows it |
| `0x06` | Dim on/off (same as the front-panel button) | status byte 3 follows it |
| `0x0b` | bit mask of analogue outputs that Mute silences | with output 3 in the mask, Mute took it from -15.7 dB to below -130 dB |
| `0x0c` | bit mask of analogue outputs that Dim attenuates | with output 3 in the mask, Dim took it to -33.7 dB (18 dB cut) |
| `0x0a` | bit mask of analogue outputs whose level is held against software changes | with output 3 in the mask, a new level written to it had no effect (stayed at -25.7 dB after writing -20 dB); on removing the bit the written level applied at once (-35.7 dB) |

Each mask is written as a 32-bit little-endian value, bit *n* for analogue
output *n* + 1. Removing the bit restored the earlier behaviour every time.
The masks can be read back with the same request in the IN direction, although
MixControl never does: values written as 3, 7 and 515 read back as 3, 7 and
515 (four bytes, little-endian). Mono reads back too (GET CUR, one byte). On this unit outputs 1/2 behaved as if all
three masks were `0x003`.

**What MixControl's ten monitor buttons send** (static analysis of the
monitor object, `0x40d3c6`-`0x40d5db`, and matching users' descriptions of
the blue / silver / red states):

| Button state | Stored value | Sent for that output |
|---|---|---|
| blue (follows the monitor section) | positive | bit set in masks `0x0a`, `0x0b` and `0x0c`; level set to the monitor level (unit `0x0a`, control 2) |
| silver (independent) | zero | bit clear in all three masks; level set to 0 dB |
| red (muted) | negative | bit clear in the masks; that output's own mute set (unit `0x0a`, control 1) |

MixControl writes all three masks together. Output levels are sent as
`dB * 32768 / 127`.

**Mono per output pair: confirmed.** Unit `0x14`, control `0x0a`, channel
*pair* (1 = outputs 1/2 ... 5 = outputs 9/10), one byte, `bRequest` CUR. With
a tone on output 3 only and the control on for pair 2, outputs 3 and 4 both
carried it at -21.7 dB (the sum of the two channels, halved); other pairs'
controls had no effect on 3/4. MixControl sets it for pairs whose two
buttons are both blue.

**Unit `0x14`, control 8, channel 1: stores a bit, purpose not found.** It
reads back what is written (off after power-on here). Switching it on changed
nothing measurable on output 3, silver or blue. The default snapshot's
monitor `pad` attribute and the exe's "-20dB" / "Pad" labels suggest a
monitor pad acting on outputs 1/2 only; not verified.

**The monitor knob appears to act on outputs 1/2 only, in firmware.** No
register tried here brought another output under it: with output 3 in mask
`0x0a` (alone, or in all three masks) its level did not move when the knob
was turned from -30 to -70 dB. Outputs 1/2 are presumably in `0x0a` so that
the knob, not the computer, sets their level; that was not tested by
removing them. The ordinary output
level and mute controls (unit `0x0a`) were measured on the same loop and are
exact: -10 and -20 dB settings gave -10.0 and -20.0 dB, and the switch gave
silence. So a program can make any output follow the knob by reading the
knob's attenuation from status byte 0 and writing that output's level, which
is probably what MixControl's ten output buttons did for outputs beyond 1/2
(inferred, not shown in the binary).

**Read-back after power-on is wrong for levels (existing driver bug).**
After a power cycle the unit answers "0 dB" for mixer gains it has not been
sent since power-on, whatever it is really doing, and the driver caches that
answer. Measured: the driver showed input 3 into Mix 1 at 0 dB while a tone
on that input reached no mix. Because the driver then treats a request for
0 dB as "no change" and sends nothing, `alsactl restore` (and any other
restorer) silently fails to restore every mixer gain that should be at 0 dB.
Writing a different value first gets through; ScarlettMix's restore does
that now. A driver fix (always send level and switch writes) is in the
patch and was tested: after a power cycle one plain write of 0 dB reached
the unit, confirmed by the mix meter. The service's forced restore was
verified the same way on that power cycle. Routing reads back correctly. A driver reload without a power
cycle reads gains back correctly, so the unit does report values it has
been sent.

**Driver behaviour noticed along the way.** After a fresh driver load, all
`Master N Playback Switch` controls read "off" although the outputs are not
muted: the driver asks the unit for each mute state and gets a non-zero
answer. In normal use `alsactl restore` writes "on" over this when the card
appears, which hides it.

## What MixControl sends

### Level meters

Routine `0x459888`. One read per bank; the register number picks the bank
and the selector is 0.

| wValue | Bank | Count used by MixControl | Kernel source comment |
|---|---|---|---|
| `0x0000` | hardware inputs | set at run time | "18*2byte: peak meter (inputs)" |
| `0x0001` | mix buses | 8 (`0x4746b4`) | "8(?)*2byte: peak meter (mix)" |
| `0x0003` | DAW playback | set at run time | "6*2byte: peak meter (pcm/daw)" |

- Reply is `count` little-endian 16-bit values; the request length is `2 × count`.
- MixControl shifts each value right by 4 before use (`0x459941`), so the
  useful level is 12 bits, 0–4095.
- On failure it zeroes the meters rather than showing stale values.

The bank numbers agree exactly with the kernel source comment. Not
established: the conversion from the 12-bit value to dB, the exact counts
for the input and DAW banks on the 18i20 (expected 18 and 20; the kernel
comment's 6 is from a smaller model), and how often MixControl polls.

### Save to hardware

Routine `0x458f5c`: write 1 byte `0xa5` to register `0x5a`
(`wValue = 0x005a`). This stores the current routing, mixer and monitor
settings in the interface so they survive a power cycle. The kernel source
documents the identical command but never issues it.

A sibling routine `0x458f21` writes `0xb5` to register `0x5b`. The exe's
error message for it is "Failed to send debug command to flash" (beside
"Failed to persist device settings" for the save command), so it is a
developer command aimed at the unit's flash memory. What it does is not
stated anywhere in the binary. It must not be sent.

### Monitor knob, dim and mute

The interface can run its outputs from the front-panel knob, Dim and Mute
(hardware control), or leave them to software. The snapshot file holds this
as `hwc` (hardware control on/off), `monset` (one entry per analogue output)
and a monitor gain.

| Register | Dir | Bytes | Routine | Meaning |
|---|---|---|---|---|
| `0x04` | read | 4 | `0x459097`, `0x459164` | front-panel status word |
| `0x05` | write | 1 | `0x459265` | mute, when hardware control is on |
| `0x06` | write | 1 | `0x4592c0` | dim, when hardware control is on |
| `0x0a` | write | 4 | `0x459216` | bit mask of analogue outputs 1–10 |
| `0x0b` | write | 4 | `0x4591a3` | bit mask of analogue outputs 1–10 |
| `0x0c` | write | 4 | `0x4591a3` | same mask as `0x0b`, written together |

- **Status word.** Byte 0 is the knob position as attenuation: MixControl
  negates it to get dB (`0x4590d5`). Bytes 2 and 3 are two on/off flags that
  it copies into the monitor object and compares with the on-screen Dim and
  Mute state; which byte is which is not established.
- **Masks.** Bit *n* is set when `monset[n] > 0`. The mask for `0x0a` is
  built by a different caller from the one for `0x0b`/`0x0c`, so they are
  separate selections (plausibly: outputs that follow the knob, outputs that
  follow Mute, outputs that follow Dim), but which is which is not
  established.
- **With hardware control off**, mute and dim are done with ordinary volume
  requests instead: mute is unit `0x0a` control 1 channel 0, and dim sets
  unit `0x0a` control 2 channel 0 to −5160 (−20.2 dB in 1/256 dB units).
  Per-output mutes are unit `0x0a` control 1, channels 1–10 (`0x459041`).

### Gain resolution (not actually missing)

The matrix gain routine `0x45a1fe` converts the fader's float dB with a
truncating conversion and shifts left by 8, so MixControl only ever sends
whole decibels to the mixer. ScarlettMix's 1 dB steps match the original
here. Output volumes are different: `0x45960c` scales as
`dB × 32768 / 127`, giving sub-dB steps, while the kernel driver exposes
1 dB steps for those.

One difference worth knowing: MixControl addresses a matrix cell as
`wValue = 0x0100 + input × 8 + mix` (control selector 1, as the USB audio
specification defines for a mixer unit) while the kernel driver uses
`input × 8 + mix` with selector 0. The kernel form demonstrably works on the
test device, so the interface appears to ignore the selector there.

### Sample rate and clock

No clock or sample-rate request goes through the generic request routine;
MixControl asks the TC Applied Technologies driver to do it. The kernel
driver already handles both (unit `0x28` clock source, unit `0x29` sample
rate), so nothing is missing at the protocol level.

## What the kernel driver has today

`sound/usb/mixer_scarlett.c` (upstream master, also the 7.2.7 module on the
test machine):

- Documents the meter banks and the save command in its header comment.
- Has a `MEM` read helper, `scarlett_ctl_meter_get`, but uses it only for
  `Sample Clock Sync Status` (register 2, 1 byte).
- Registers no meter, save, or monitor control. Registers `0x04`–`0x0c` are
  not mentioned at all; those are new from this analysis.

## Delivery

A program cannot send these itself while audio is in use. usbfs requires a
process to claim an interface before sending a class request to it
(`check_ctrlrecip` → `checkintf` → `claimintf` in `drivers/usb/core/devio.c`),
and the control interface is already owned by `snd-usb-audio`. Detaching the
driver would stop all audio. On the test machine the device node is also
writable only by root.

So the features belong in the kernel driver, as new ALSA controls that
ScarlettMix then picks up like the existing ones:

| Control | Type | Request |
|---|---|---|
| `Save To HW` | write-only trigger | write `0xa5` to register `0x5a` |
| `Input Level Meter`, `Mix Level Meter`, `PCM Level Meter` | read-only, volatile, integer × n | read bank 0 / 1 / 3 |
| `Monitor Hardware Status` | read-only, volatile | read register `0x04` |
| `Monitor Hardware Mute`, `Monitor Hardware Dim` | boolean | write register `0x05` / `0x06` |
| three output-mask controls | integer bit mask | write register `0x0a` / `0x0b` / `0x0c` |

The patch is small: each control is one `add_new_ctl` line plus a get or put
that calls `snd_usb_ctl_msg` with `UAC2_CS_MEM`, following the existing sync
status control. It would be built as an out-of-tree `snd-usb-audio` module
(DKMS) and could be offered upstream once verified.

### Order of work

1. Meters first. They are read-only, so a wrong guess cannot change the
   device, and a working meter on a known signal settles the counts and
   the scaling.
2. Read register `0x04` while turning the knob and pressing Dim and Mute to
   name the status bytes. Still read-only.
3. Save To HW. One well-documented write, confirmed by two independent
   sources.
4. Monitor masks and hardware mute/dim last: these are writes whose exact
   meaning is partly inferred, so test with monitors turned down.

Installing a kernel module needs root, and reloading `snd-usb-audio`
interrupts audio briefly.

## Without a kernel patch

The preferred direction for this project. Nothing below needs a patched
kernel; the raw-USB items need the udev rule in `contrib/`.

| Need | Route | State |
|---|---|---|
| Settings back after a power cycle | a background service (`--daemon`) remembers the interface's state on every change and reapplies it whenever the interface appears | built and running; recording verified on the real interface, restore after a real power cycle not yet observed |
| Save to hardware, monitor-knob setup | detach `snd-usb-audio` from the control interface, send the request over usbfs, reattach; audio drops for a moment | not built; needs the udev rule first |
| Meters | measure levels in software from the PipeWire capture and playback streams | not built; no mix-bus meters this way |

`contrib/usb_probe.py` checks, with read requests only, whether raw access
works and confirms the kernel refuses the control interface while it is in
use.

### The unused-interface idea: do not use

usbfs only checks the interface number in the low byte of `wIndex`, so a
request addressed to an interface no driver has bound passes the kernel's
check. On the 18i20 the only such interface is number 5, firmware update.

This was tried once on 2026-10-13 with a single read (`0xa1`, request 3,
`wValue` 0, `wIndex` `0x3c05`, 36 bytes). The transfer failed with `EPROTO`,
the unit dropped off the USB bus at once, and it would not enumerate again
("device not responding to setup address") until it was power-cycled. No
request should be sent to that interface. The option has been removed from
the probe.

Result of the same probe for the normal route: with the udev rule in place
the device node is writable, and a read addressed to the control interface
is refused with `EBUSY` while `snd-usb-audio` is bound, as the kernel source
predicts.
