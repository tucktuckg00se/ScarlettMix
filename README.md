# ScarlettMix

Mixer and routing control for first-generation Focusrite Scarlett interfaces
on Linux, modelled on the discontinued Scarlett MixControl.

Developed and tested on a Scarlett 18i20 (USB `1235:800c`). The 6i6, 8i6,
18i6 and 18i8 use the same kernel driver and should work, but have not been
tried on hardware.

## Run

    python -m scarlettmix            # first supported Scarlett found
    python -m scarlettmix --demo     # simulated 18i20, touches no hardware
    python -m scarlettmix --status   # print the current setup and exit
    python -m scarlettmix --load my-setup.json   # apply a preset and exit
    python -m scarlettmix --save my-setup.json   # write the current setup to a preset
    python -m scarlettmix --restore  # put back the remembered setup once
    python -m scarlettmix --daemon   # background service (see below)

Requires Python 3.10+, PySide6 (`python-pyside6` on Arch) and `libasound`.
Your user needs access to the sound card, which a normal desktop login has.
No extra driver is needed: the kernel's `snd-usb-audio` already speaks the
interface's control protocol, and this program drives it through ALSA, so it
works while PipeWire or JACK is using the card.

To get a `scarlettmix` command, install the package (for example
`pipx install --system-site-packages .`); `scarlettmix.desktop` can then be
copied to `~/.local/share/applications/`. Installing is optional and untested
here; running from the checkout is what has been exercised.

## What it does

The window follows the original: mixer on top, routing below it, monitor
section at the right, with the original's names (DAW 1, Mix 1, Line Output 7).

- **Mixer**: eight mixes, each with a channel strip per mixer input
  (source, level, mute, solo) and a master. Adjacent mixes can be linked as
  a stereo mix with pan, and adjacent inputs as a stereo channel with
  balance. Faders use the original's taper. The master strip's send menu
  puts the mix on any output pair, including the two headphone outputs
  (Line 7/8 and 9/10 on the 18i20), and Copy Mix To duplicates a mix.
- **Routing**: source for every output in the original's grid, with the
  Clear, DAW Tracking, Zero Latency Tracking and Mixing presets, plus a tab
  for DAW capture channels.
- **Levels**: master in the monitor section, per-pair level and mute on a tab.
- **With the patched driver**: level meters beside the faders; File ▸ Save to
  Hardware; and the monitor section's Dim, Mute and ten output buttons. Blue
  outputs follow the front-panel knob, Mute and Dim; silver ones are
  independent; red ones are muted. Each click moves to the next state.
  Mono, the monitor Pad and the original's Monitor Preset menu are there too. The knob is followed in
  software by the background service for outputs other than 1/2.
- **Clock**: source selection, lock indicator, current sample rate.
- **Presets**: save and load complete setups as JSON, and open original
  MixControl snapshot files (`.18i20` and friends).

Starting the program never changes the device: it reads what is there. If
another program (alsamixer, for example) changes something, the display
follows.

## Differences from the original

- Output levels move in 1 dB steps (the original is finer); that is what the
  kernel driver offers. Mixer levels are whole decibels in the original too.
- Level meters, save to hardware and the monitor section's output buttons
  need controls the stock kernel driver does not have. They appear when the
  patched driver in `../kernel` is loaded (`./load-test.sh`; a reboot or
  `./revert.sh` goes back to stock) and are absent otherwise. Details and
  measurements are in `docs/missing-features.md`.
- The sample rate is shown but not set here; on Linux the audio server or
  application chooses it.

## Keeping settings across power cycles

The interface only stores settings permanently on a command the Linux driver
cannot send, so after losing power it comes back with old ones. The
background service covers that from the computer's side:

    cp contrib/scarlettmix.service ~/.config/systemd/user/
    systemctl --user enable --now scarlettmix.service

It remembers the interface's complete state a second after anything changes
it (ScarlettMix, alsamixer, or any other program) and puts that state back
each time the interface appears: at login, on replug, after a power cycle.
It uses only the ALSA controls and about 20 MB of memory.

Two consequences: settings changed while the interface was attached to a
different computer are replaced by this computer's remembered ones when it
returns, and the interface still does not remember them when used standalone.

## Layout

    scarlettmix/alsa.py      ctypes binding to the ALSA control API
    scarlettmix/backend.py   real card and simulated card
    scarlettmix/device.py    device structure discovered from control names
    scarlettmix/laws.py      fader and pan curves from the original
    scarlettmix/mixer.py     faders/pan/mute/solo/stereo -> hardware gain grid
    scarlettmix/presets.py   presets and MixControl snapshot import
    scarlettmix/daemon.py    background service: remember and restore
    scarlettmix/ui/          PySide6 interface
    docs/mixcontrol-protocol.md   how the original works, with evidence
    docs/missing-features.md      meters, save and monitor knob: protocol and plan
    driver/                       patched kernel driver: patch, build, load, revert

Tests: `python -m pytest`. They run against the simulated device only.

`tests/data/Default.18i20` is a snapshot file shipped with Focusrite's
MixControl, kept as a test fixture. Remove it before publishing this
repository.
