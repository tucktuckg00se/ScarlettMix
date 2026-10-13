# How Scarlett MixControl works

Findings from static analysis of `Scarlett_Mix_Control_1_10_Usb_4.65.5.exe`
(SHA-256 `e78517e5…cf0721`), cross-checked against a Scarlett 18i20
(USB `1235:800c`) on Linux. Addresses are virtual addresses in
`Scarlett MixControl.exe` (SHA-256 `8959efe5…a53da2`, 32-bit PE, image base
`0x400000`), unpacked from the Inno Setup installer.

Nothing here was observed running: the Windows side is static analysis only.
The Linux side was read from the live device.

## Summary

MixControl is a thin GUI over standard-shaped USB Audio Class 2 control
requests. It keeps no secret state in the driver: every fader and routing
choice becomes a small `SET CUR` request to a numbered unit inside the
interface. The Linux kernel's `mixer_scarlett` driver sends the same requests
to the same units, which is why a Linux replacement can be built on ALSA
controls without any new driver.

## Layers

| Layer | Evidence |
|---|---|
| GUI: VSTGUI widgets (`CFaderControl`, `CPanControl`, `ChannelStrip`, `MasterStrip`, `Routing`, `Monitor`) | C++ type names in the binary |
| State model: `FFSigSet` (signal tables), `FFMatrix` (mixer), `FFMonitor`, `Saffire18i20Router`, `Saffire18i20Device` | type names; per-device template instances for 18i20, 18i8, 6i6 and three "Tracker" models |
| Snapshots: TinyXML (`USB18i20Snapshot` → `Router`, `Mixer`, `Monitor`, `System`) | `smc/Default.18i20` shipped in the installer |
| Hardware access: `WinHWDevice` → `Pal::DiceDevice` → `Pal::DiceRootDriverWin` | vtables at `0x4a9c8c`, `0x4a9b04`, `0x4a9ad4` |
| Kernel driver: TC Applied Technologies USB audio driver (`Focusriteusb.sys`) | `\\.\TcatDriverLogger`, PDB path `…\HMP_Driver_Build\…` |

## Transport

- The driver is found through a device-interface GUID with `SetupDi*` and opened
  with `CreateFileA` (`0x45519e`).
- Every command is one `DeviceIoControl` with code **`0x222008`**
  (`0x454972`); a second code, `0x22200c`, is an overlapped wait for
  notifications (`0x454498`).
- `DiceRootDriverWin` (`0x454c4d`) prefixes each request with a 16-byte header:
  two 32-bit words identifying the device, a 16-bit category, a 16-bit
  sub-command, and a zero word.
- USB control requests go out through `0x45a753`, which builds a 5-byte record
  `{direction, unit, control selector, channel, bRequest}` followed by the
  payload, and hands it to `0x45a632` with the constants `0x401` and `0x16`.

## Commands

Arguments of each call to `0x45a753`: direction, unit, bRequest, control, channel, length.

| Call site | Unit | bRequest | Control | Channel | Bytes | Purpose |
|---|---|---|---|---|---|---|
| `0x459c55` set, `0x459be0` get | `0x32` | 1 (CUR) | 6 | mixer input | 2 | source feeding a mixer input |
| same | `0x33` | 1 | 0 | output channel | 2 | source feeding a physical output |
| same | `0x34` | 1 | 0 | capture channel | 2 | source feeding a DAW input |
| `0x45a248` set, `0x45a1d2` get | mixer unit | 1 | 1 | input × 8 + mix | 2 | one cell of the mix gain matrix, whole dB × 256 |
| `0x4595ef`, `0x459655` set, `0x45946d` get | `0x0a` | 1 | 2 | n + 1 | 2 | output volume |
| `0x45907e`, `0x4592b1` set | `0x0a` | 1 | 1 | n / 0 | 2 | output mute / master mute |
| `0x459315` set | `0x0a` | 1 | 2 | 0 | 2 | master volume, written as 0 or −5160 (−20.2 dB in 1/256 dB units; probably the Dim button) |
| `0x459780` set, `0x459748` get | `0x01` | 1 | caller's | n + 1 | 2 / 1 | per-input switches |
| `0x459927` get | mixer unit | 3 (MEM) | 0 | 0, 1 or 3 (bank) | 2 × n | level meters: inputs, mixes, DAW |
| `0x459dcd` get | mixer unit | 3 | 0 | 2 | 1 | clock lock status |
| `0x458f8a` set | mixer unit | 3 | 0 | `0x5a`, data `0xa5` | 1 | save settings to the hardware |
| `0x4591ea`, `0x45920b`, `0x45925b` set | mixer unit | 3 | 0 | `0x0a`–`0x0c` | 4 | monitor output masks |
| `0x4590c8` get | mixer unit | 3 | 0 | `0x04` | 4 | front-panel status (knob, dim, mute) |
| `0x4592b1`, `0x459315` set | mixer unit | 3 | 0 | `0x05`, `0x06` | 1 | hardware mute, dim |

For `MEM` requests the channel column is a register number. The unit choice for routing is a three-way switch at `0x459b58`: type 1 →
`0x32` with control 6, type 3 → `0x34`, otherwise `0x33`.

## Signal numbering

`Saffire18i20_IpSigTab` (`0x4a3824`, 47 entries of 40 bytes) gives every source
its routing value:

| Source | Value |
|---|---|
| DAW (PCM) 1–20 | 0–19 |
| Analogue in 1–8 | 20–27 |
| S/PDIF in 1–2 | 28–29 |
| ADAT in 1–8 | 30–37 |
| Mix 1–8 | 38–45 |
| Off | `0xff` |

Each entry carries separate values for 44.1/48 kHz and 88.2/96 kHz; ADAT 5–8
are marked unavailable at the higher rates. `Saffire18i20_OpSigTab`
(`0x4a2e84`, 57 entries) lists destinations: 18 to-host, 10 analogue,
2 S/PDIF, 8 ADAT, 18 mixer inputs, and Off.

## Gain curves

Faders are stored as 0–1 positions and converted with (`FaderLaw`, `0x409fd7`):

    dB = min + range × (B^pos − 1) / (B − 1)

| Curve | Range | B | Initialised at |
|---|---|---|---|
| Mixer channel and mix master | −128 … +6 dB | 0.201923² | `0x4010b9` |
| Pan (far side) | −134 … −0.01 dB | 0.052632² | static initialiser for `0x4c3b78` |

Position 0.775137, the value in the shipped default snapshot, evaluates to
0.00 dB. Pan is a balance control (`0x4686a0`): the side panned toward stays
at unity and the other side follows the pan curve at twice the pan position,
so a centred input is at full level in both buses.

## Snapshot files

`Default.18i20` is XML. Router cross-points are `source-type source-index
dest-type dest-index`, with types 0 analogue, 1 ADAT, 2 S/PDIF, 3 DAW,
5 mix/mixer, 6 off. The mixer section stores, per mix, a master `gain` and
per-channel `g` (fader position), `p` (pan), `m` (mute) and `s` (solo);
`busms` and `chanms` are bit masks of stereo-linked buses and channels.

## Cross-check against Linux

`/proc/asound/card0/usbmixer` on the test machine (kernel 7.2.7) shows the
kernel driver using the same units for the same jobs:

| Unit | Kernel control |
|---|---|
| 10 (`0x0a`) | `Master N Playback Volume` / `Switch` |
| 40 (`0x28`) | `Sample Clock Source` |
| 50 (`0x32`) | `Matrix NN Input Playback Route` |
| 51 (`0x33`) | `Master NL/NR Source Playback Enum` |
| 52 (`0x34`) | `Input Source NN Capture Route` |
| 60 (`0x3c`) | `Matrix NN Mix X Playback Volume`, `Sample Clock Sync Status` |

The ALSA enum order (`Off`, `PCM 1–20`, `Analog 1–8`, `SPDIF 1–2`,
`ADAT 1–8`, `Mix A–H`) is the table above shifted by one for `Off`.

## Not established

- The numeric value of the "mixer unit" on the Windows side is read from a
  device descriptor object at run time; `0x3c` is inferred from the Linux
  driver using that unit for the same controls.
- Several details of meters and the monitor registers; see
  `missing-features.md`, which covers those features in full.
- How `chanms` stereo-linked channels are panned; the importer assumes hard
  left/right, which matches the shipped default (DAW 1/2 into the monitor mix).
- Whether the mix master gain is folded into the matrix exactly as this
  project does it.
