# Headroom

Battery level for the **Skullcandy Crusher PLYR 720** in the [Noctalia](https://github.com/noctalia-dev/noctalia-shell) bar, read over the headset's 2.4 GHz USB dongle.

Pairing the headset to the PC over Bluetooth would also surface a battery level, through BlueZ and UPower, with no custom code at all. That is not what this is for. The PLYR 720 has exactly one Bluetooth slot, and the point here is to spend it on a phone while the PC talks to the dongle.

Skullcandy ships no Linux software, and the headset is not supported by [HeadsetControl](https://github.com/Sapd/HeadsetControl).

## Status

The transport is solved and proven. Battery delivery is not fully characterised yet.

| Capability | State |
| --- | --- |
| Talk to the dongle over its vendor protocol | Working |
| Read firmware identity and Bluetooth address | Working |
| Detect the headset linking and unlinking | Working |
| Read battery level | Partial, see below |

The dongle **refuses** an on-demand battery request. Opcode `0x0CD6` is answered with a status-only acknowledgement carrying `0x02`, for every argument tried, whether the audio link is idle or actively streaming. The level instead arrives unsolicited, as an indication, and has so far only been observed in the burst of frames that is already queued when a listener attaches.

Headroom therefore does not poll. It holds the channel open, keeps every frame, and publishes the level whenever the dongle volunteers one. Until the trigger is pinned down, the bar can show a level that is some minutes or hours old, and the widget fades it and puts the timestamp in the tooltip rather than pretending it is live.

Every frame is written to a log, which is the raw material for closing this gap:

```bash
bin/headroomctl.py --frames
```

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
| `0x0CD6` | Battery level, as a `0x5D` indication | HyperHeadset |
| `0x0F92` | Firmware debug log | observed here |
| `0x2CB1` | Headset link state | observed here |

`0x0CD5` returns `status, earbud selector, 6-byte address`. The selector byte is meaningless on a headset and returns junk, which is a good way to waste an hour if you assume the payload is just an address.

`0x2CB1` is undocumented elsewhere. Byte 2 is the link flag, bytes 4 through 9 are the headset's own address, little-endian:

```
00 02 00 01  5e 1f b5 f8 bb cd  ff 00    headset gone
00 02 01 01  5e 1f b5 f8 bb cd  80 01    headset linked
```

### A warning

This same channel reaches flash erase and firmware update. Reading identity values is harmless; a blind opcode sweep is not, and could unpair or brick the headset. Nothing here writes, and the ranges `0x0400-0x04FF` and `0x1C00-0x1CFF` are refused outright.

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
