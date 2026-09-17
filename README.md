# Headroom

Talks to the **Skullcandy Crusher PLYR 720** dongle over its vendor protocol from Linux, and reports headset link state in the [Noctalia](https://github.com/noctalia-dev/noctalia-shell) bar.

It was built to read the battery level. It cannot: the dongle does not expose one. See Status.

Pairing the headset to the PC over Bluetooth would also surface a battery level, through BlueZ and UPower, with no custom code at all. That is not what this is for. The PLYR 720 has exactly one Bluetooth slot, and the point here is to spend it on a phone while the PC talks to the dongle.

Skullcandy ships no Linux software, and the headset is not supported by [HeadsetControl](https://github.com/Sapd/HeadsetControl).

## Status

**The dongle does not expose the headset's battery.** Everything else works. This is a negative result, arrived at by measurement rather than assumption, and it is documented here so nobody repeats the search.

| Capability | State |
| --- | --- |
| Talk to the dongle over its vendor protocol | Working |
| Read firmware identity and Bluetooth address | Working |
| Detect the headset linking and unlinking | Working |
| Read battery level | **Not available** |

### How that was established

An earlier version of this project reported a battery percentage. It was wrong. The value came from opcode `0x0CD6`, taken from the HyperHeadset project, which reads it over Bluetooth on a HyperX headset. On this dongle that opcode emits long monotonic runs, dozens of values within a single second, stepping by one, and the identical run repeats across unrelated events. Taking the last value of each run produced a number that looked like a battery and was not one.

A controlled elimination run settled it. Each action was performed deliberately while every frame was recorded:

| Action | Frames produced |
| --- | --- |
| Idle | 0 |
| Bass slider through its full range | 0 |
| Volume up and down | 0 |
| **Power cycle** | **40** |
| Charger plugged in | 0 |
| Charger unplugged | 0 |

Only the link transition says anything at all. Plugging a charger into the headset produces **nothing**, which is close to conclusive on its own: a dongle that tracked the headset's battery would have a charging state to report, and this one is silent.

The remaining candidate, `0x2CD0`, is not a battery either. Across one power cycle it reported:

```
75  85 90 95 100  90 80 65 50 35 25 20 15 10 5 0
```

That is a fade envelope ramping up and back down over about a second, almost certainly the startup sound or haptic ramp. `0x0CD6` behaves the same way. Both are continuous controls being echoed, not state.

### Why `0x0CD6` is refused, in hindsight

The clue was there from the first hour and was misread. `0x0CD6` asks *the chip you are talking to* for its battery. Over USB that chip is the **dongle**, and the dongle has no battery, so it answers with an error. It was never going to work, and no argument to it would have helped. Reading the headset's battery would need a relay command in the dongle's own `0x2Cxx` family, and nothing resembling one has been observed.

If you want the headset's battery on Linux today, pair it over Bluetooth: BlueZ and UPower expose it with no custom code. That costs the headset's single Bluetooth slot, which is exactly what this project existed to avoid.

## The protocol

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
| `0x0CD6` | A continuous control, ramping. **Not battery**, despite HyperHeadset reading it as one over BLE on another device | observed here |
| `0x2CD0` | A fade envelope, ramping to 100 and back to 0 | observed here |
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
