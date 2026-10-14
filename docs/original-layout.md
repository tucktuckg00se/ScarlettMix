# The original's screen layout

Read from the skin images and label tables embedded in
`Scarlett MixControl.exe` (91 PNG resources), and from the shipped default
snapshot. The artwork itself is Focusrite's and is not copied into this
project; only the arrangement is followed.

## Window

One fixed window, 1019 × 573 (`BACKGROUND`), no tabs between sections:

| Area | Skin | Contents |
|---|---|---|
| Top, full width | `BGMIXER` 1019 × 330 | mix tabs, channel strips, mix master strip |
| Bottom left | `ROUTINGHEADER` 729 × 41, `BG18I20R` 729 × 198 | routing |
| Bottom right | `MONITOR` 182 × 236 | monitor knob and buttons, device status |

## Mixer

- Tabs along the top, one per mix (`TAB`); a stereo mix gets a wider tab
  (`TABSTEREO`).
- Channel strips: a mono pair is two 52-pixel channels (`STRIP`), a stereo
  channel is one 104-pixel strip with two source selectors (`STRIPSTEREO`).
  Top to bottom: source selector, pan (L–C–R), fader with marks at +6, 0 and
  ∞ beside a meter, a row of buttons (`MUTEON`, `SOLOON`, `PFLON`,
  `STEREOON`), name field.
- Mix master strip (`MASTERSTRIP`, `MSTRSTRPSTRO`): mix name, then a selector
  for where the mix goes, then fader and buttons. In the default snapshot a
  stereo mix offers Off, Monitor 1/2, Line 3/4, 5/6, 7/8 and 9/10.
- "Copy Mix To..." exists as a menu entry.

## Routing

- Header row: "Routing Preset..." menu, then `source → Monitor Output 1`
  and `→ Monitor Output 2`.
- Panel, four cells per row, each `source → name`:
  Line Output 3–6, Line Output 7–10, ADAT Output 1–4, ADAT Output 5–8,
  SPDIF Output 1–2.
- Headphone icons sit beside Line Output 7 and Line Output 9: headphone
  socket 1 plays Line 7/8 and socket 2 plays Line 9/10.
- Preset names in the binary: Clear, DAW Tracking, Zero Latency Tracking,
  Mixing, Loopback. What each one sets was not recovered from the code.
- There is no selector for DAW capture channels; the original leaves them
  fixed (hardware inputs in order).

## Monitor

Large knob with a dB readout, Dim, Mute and Mono buttons, left and right
mutes, ten small buttons numbered 1–10 (which outputs the knob controls),
and a "Monitor Preset..." menu. `HWCONTROLMSG` is shown when the front-panel
knob is in charge.

## Names

Sources: `Anlg In 1–8`, `SPDIF L/R`, `ADAT In 1–8`, `DAW 1–20`, mixes 1–8.
Mixes are numbered, never lettered.

## What ScarlettMix does with this

- Same three areas in the same places; the lower-left area has extra tabs
  for DAW inputs and per-output levels, which the original lacks.
- Original names on screen; driver names (`PCM`, `Mix A`) stay internal.
- The routing grid keeps the original's four-wide rows but adds a heading per
  row, and shows the pairs that feed headphone sockets as their own
  "Headphones" row (`Headphones 1 L`, with `Line Output 7` beneath) instead
  of an icon beside the line output. The preset menu shares the monitor row.
- The mix selector is a multi-choice menu rather than a single choice, so
  one mix can go to several outputs, and it includes the digital outputs.
- The four routing presets are implemented from the Focusrite user guide as
  remembered, not from the binary: Clear turns outputs off, DAW Tracking
  sends DAW 1/2 to every analogue pair, Zero Latency Tracking sends Mix 1/2
  to every analogue pair, Mixing sends DAW *n* to output *n*. Loopback is
  omitted.
- Stereo channel strips and Copy Mix To work as in the original. A stereo
  channel applies in every mix; its switch sits on the left input's strip.
- With the patched driver: meters, Dim, Mute and the ten output buttons with
  the original's blue / silver / red states. Each click moves to the next
  state; the original toggles blue and red and needs shift-click for silver. Silver sets the output to 0 dB, as in the
  original.
- Also with the patched driver: Mono, Pad and the Monitor Preset menu.
- The knob assignment works as in the original's hardware-control mode: it
  is stored in the unit and takes effect after a save and a restart of the
  unit, which is why every Save to Hardware asks for the interface to be
  switched off and on. Until then the program follows the knob itself for
  newly blue outputs while it is open, and gives them back the level they
  had before when it closes.
- Not in the original: every control is drawn solid when its setting is
  saved to hardware and as an outline when it is not, with Revert and a
  Save / Discard / Cancel question on closing.
- Not present: PFL and an on-screen monitor knob (the knob position is shown).
