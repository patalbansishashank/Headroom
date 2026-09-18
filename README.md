# Headroom

Battery levels for wireless peripherals, as ring gauges in the [Noctalia](https://github.com/noctalia-dev/noctalia-shell) bar.

One gauge per device: the device's own icon, fully tinted by how much charge is left, with a slim upright bar beside it carrying the exact level.

The icon takes the colour rather than a fill, because colour only works if it occupies real area. A red sliver five pixels tall at the bottom of a filled glyph is not a warning anyone will notice; a whole red headset is.

| Device | How it is read | Refreshes |
| --- | --- | --- |
| Skullcandy Crusher PLYR 720 | Airoha RACE over its 2.4 GHz dongle | when the headset links |
| WLmouse Sword X (and siblings) | COMPX page/command over the receiver | while the mouse is in use |

Neither is supported by any existing Linux tool. The headset is unknown to [HeadsetControl](https://github.com/Sapd/HeadsetControl) and Skullcandy ships nothing for Linux; the mouse's own configurator is a Windows application and a WebHID page.

## Colour


White while there is nothing to think about, easing into amber as the charge runs down and into red when it is urgent. The bands are transitions, not steps:

```
100 ────────── 30   white
 30 ────────── 20   white fading to amber
 20 ────────── 10   amber
 10 ──────────  5   amber fading to red
  5 ──────────  0   red
```

"White" is the theme's foreground rather than literal white, so the gauge stays legible if the bar is ever light-on-dark.

## Adding a device

Write one class in `bin/headroom_sources.py` and add it to `SOURCES`. Nothing else changes: the daemon publishes whatever sources report and the bar renders one gauge per entry.

There are two shapes, because hardware differs in kind:

- **`PolledSource`** answers on demand. The daemon asks on a timer.
- **`PushedSource`** owns its own connection and speaks when it chooses.

Both devices here are pushed, for different reasons. The headset dongle reports only at link-up and refuses to be asked. The mouse *can* be asked, but sleeps aggressively and simply does not answer while asleep, so polling it on a timer mostly burns retries against a device that is not listening. Instead its source waits on the mouse's own input node, which costs nothing until the mouse moves, and reads the battery when the mouse is demonstrably awake, at most once every two minutes.

## Status

Working. The battery level is real, and it is the **first** value of each burst.

| Capability | State |
| --- | --- |
| Talk to the dongle over its vendor protocol | Working |
| Read firmware identity and Bluetooth address | Working |
| Detect the headset linking and unlinking | Working |
| Read battery level | Working, refreshes when the headset links |

### The trap, and it caught this project three times

On link-up the dongle sends a descending run of indications on `0x0CD6`:

```
99 98 97
99 98 97 97 96
60 59 58 57 ... 27 26
```

**The level is the value it settles on, not the one it starts from.** The run is a gauge animation that finishes at the truth.

This project got it wrong in both directions before measuring against an independent source:

1. Took the last value. Correct, but unverified.
2. Took the first value, on the reasoning that a battery cannot sweep 32 points in a second and that an identical run repeating across link-ups was suspicious. Both observations are real; neither supports the conclusion. The sweep is an animation frame rate, and a repeated run just means the level had not changed.
3. Concluded it was not a battery at all and removed the feature.

What settles it is the level the headset reports over Bluetooth at the same moment:

| Burst | first | last | Phone |
| --- | --- | --- | --- |
| `[99, 98, 97]` | 99 | **97** | 97 |
| `[99, 98, 97, 97, 96]` | 99 | **96** | 96 |

Beware the first value specifically: because both ends of a descending run move together, first-values also trace a plausible discharge curve across a day. That curve looks convincing and reads about three points high. Only an independent reading distinguishes them.

### What it does not do

The level is pushed only when the headset links, never on request: a direct query for `0x0CD6` is refused, because over USB that asks the **dongle** for its own battery and the dongle has no battery. Charger events produce no frames at all. So the number is correct as of the last link, and the widget timestamps it rather than implying it is live. To refresh it, power the headset off and on.

## The headset protocol

The dongle is an **Airoha AB157x** running `IoT_SDK_for_BT_Audio_V5.2.0`. Its vendor HID interface carries **RACE**, Airoha's factory command protocol, reverse-engineered and published by ERNW in 2025. Skullcandy did not invent a protocol; Skull-HQ is a RACE client.

USB HID framing, confirmed against this device:

```
vendor usage page   0xFF13
report 0x06 OUT     61 data bytes   ->  b"\x06" + u16le(len) + race_bytes, NUL padded
report 0x07 IN      61 data bytes   <-  GET_REPORT(7): [0]=0x07, [1:3]=u16le len, [3:3+len]
race frame          0x05 | type | u16le length | u16le opcode | payload
                    length counts the 2-byte opcode plus the payload
                    type: 0x5A request, 0x5B response, 0x5C request-no-reply, 0x5D indication
```

Two traps will defeat a naive reader:

- **Frames do not align to report boundaries.** The input is a byte stream that must be reassembled across reports, not parsed one report at a time.
- **The firmware streams its own debug log** as indications on opcode `0x0F92`, interleaved with everything else. It has to be filtered out.

A third trap cost real time here: the frames worth having arrive in a **backlog** that is already queued before you attach. Any code that opens the device and drains before parsing deletes exactly the events it is looking for.

### Opcodes

| Opcode | Meaning | Source |
| --- | --- | --- |
| `0x0301` | SDK version | ERNW |
| `0x1E08` | Build version | ERNW |
| `0x0CD5` | Bluetooth address | ERNW |
| `0x0CD6` | Battery level, as the **last** value of a descending burst | HyperHeadset, verified here |
| `0x2CD0` | A fade envelope, ramping to 100 and back to 0. Not battery | observed here |
| `0x2C80` | Link up (`03`) / link down (`01`) | observed here |
| `0x0F92` | Firmware debug log | observed here |
| `0x2CB1` | Headset link state | observed here |

`0x0CD5` returns `status, earbud selector, 6-byte address`. The selector byte is meaningless on a headset and returns junk, which is a good way to waste an hour if you assume the payload is just an address.

`0x2CB1` is undocumented elsewhere. It is indication-only: sent as a request it draws no reply at all, unlike `0x0CD6`, which is explicitly refused. The dongle therefore knows `0x0CD6` and is declining it, rather than not implementing it.

Byte 2 is the link flag, bytes 4 through 9 are the headset's own address, little-endian (shown here as `aa bb cc dd ee ff`):

```
00 02 00 01  aa bb cc dd ee ff  ff 00    headset gone
00 02 01 01  aa bb cc dd ee ff  80 01    headset linked
```

### A warning

This same channel reaches flash erase and firmware update. Reading identity values is harmless; a blind opcode sweep is not, and could unpair or brick the headset. Nothing here writes, and the ranges `0x0400-0x04FF` and `0x1C00-0x1CFF` are refused outright.

### Cost

The daemon is idle-cheap by design, which took a second pass to achieve.

The dongle answers only `GET_REPORT`; it never pushes on its interrupt endpoint, verified by waiting on the hidraw node with a reply outstanding and seeing nothing. So reads have to be polled. But it buffers frames, so polling fast buys nothing: Headroom reads once a second at rest and drops to 5 ms the moment a frame appears, draining a burst at full speed before easing back. Idle CPU is below what `/proc` can resolve over ten seconds.

The state file is written only when a value actually changes, not on a timer. Readers derive age from the stored timestamp, so a still-correct file never needs rewriting. At rest it is not touched at all.

## Notes for plugin authors

Two things here were learned the hard way and are easy to repeat.

**Use `BarPill`, do not draw your own capsule.** A hand-rolled rounded rectangle looks right in isolation and wrong in the bar. The shared pill sizes its icon at 0.48 of the capsule height, uses `radiusM` rather than a full round, and takes its border width from the theme. Matching that by eye produces a pill that sits visibly taller than its neighbours.

**A daemon outlives the shell that started it.** Restarting Noctalia leaves the old child running and holding whatever device lock it took, so the new shell's daemon can never start. This one asks the kernel to kill it when its parent goes away, and treats losing the lock as a reason to back off rather than a crash to retry every few seconds.

## Install

```bash
bash install.sh
```

Then enable **Headroom** in Noctalia under Settings, Plugins, Installed, and add the widget to a bar section.

The installer needs root once, to place a udev rule. The dongle's vendor HID node is root-only by default, and the rule grants the seat-local user access.

The `70-` prefix on that rule is load-bearing. `73-seat-late.rules` is what runs the access-control builtin, and it only acts on devices already tagged when it is evaluated. A `99-` prefix sets the tag too late and grants nothing, leaving a device that is correctly tagged and still unreadable.

## Command line

```bash
bin/headroomctl.py              # last known level
bin/headroomctl.py --json       # one JSON object
bin/headroomctl.py --identify   # firmware identity, straight from the dongle
bin/headroomctl.py --watch      # follow changes
bin/headroomctl.py --frames     # tail raw protocol frames
```

`--json` emits the shape a Noctalia CustomButton wants, so the level can go in the bar without the plugin.

## Layout

```
bin/headroom_race.py    RACE over USB HID via hidraw, no third-party modules
bin/headroomd.py        holds the session open, publishes state as JSON
bin/headroomctl.py      command line reader
udev/                   device access rule
Main.qml                supervises the daemon, republishes its state
BarWidget.qml           the bar item
Settings.qml            plugin settings
```

The daemon writes `$XDG_RUNTIME_DIR/headroom/state.json` and reconnects on its own. The dongle re-enumerates whenever the headset is powered off, so the device node disappears and returns under a new number; that is normal and handled.

## Credits

- ERNW's Airoha RACE research, which documented the protocol and its USB HID framing.
- [HyperHeadset](https://github.com/LennardKittner/HyperHeadset), where the battery opcode and its indication-based delivery are implemented for Bluetooth.

## License

MIT

## The mouse protocol

WLmouse receivers speak COMPX, the same page/command framing Lamzu receivers use. It rides a 64-byte **feature** report at report id 0, on the vendor-page interface (usage page `0xFFFF`) — not the mouse or keyboard interfaces of the same receiver.

```
request  [status=0x00][0][target][length][page][command][args...]   64 bytes
reply    same shape; status 0xA1 ok, 0xA0 pending, 0xA2 unsupported
payload  from byte 6, length from byte 3
```

Battery is `target=0x02 (mouse)`, `page=0x00 (device)`, `command=0x83`, and the payload is `[charging, percent]`.

Three things that cost time here:

- **A reply may arrive shifted by one byte**, so both offsets have to be checked.
- **`0xA0` means pending, not failure.** The receiver has accepted the request and is waiting on the mouse over RF. Read that as an error and a working device looks broken.
- **An asleep mouse never answers.** Everything addressed to `target=0x02` stays pending forever while the dongle itself answers instantly. That difference is the diagnostic: if `target=0x00` (dongle firmware) replies and `target=0x02` does not, the protocol is fine and the mouse is simply asleep.

Several interfaces of one receiver mention the vendor usage page, so matching on the page alone finds the same physical mouse more than once. The config interface is the one declaring a 64-item feature report.

### The receiver volunteers state

A separate interface (usage page `0xFFA0`, report id 4) pushes notifications the OpenMouse driver receives but never decodes. Captured on this receiver:

```
04 06 01 …        mouse connected
04 06 00 …        mouse gone
04 03 cc pp …     battery: cc = charging, pp = percent
```

So presence and level both arrive unprompted, with no request the mouse might sleep through. Headroom listens here and uses the feature-report read only as an initial probe and an occasional refresh while the mouse is moving.

Protocol reference: the [OpenMouse project](https://github.com/OpenMouse-Project/mouse-protocol)'s WLmouse driver.
