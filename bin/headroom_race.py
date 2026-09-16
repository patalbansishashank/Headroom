#!/usr/bin/env python3
"""
Airoha RACE over USB HID, spoken to a Skullcandy dongle through hidraw.

The Crusher PLYR 720 dongle is an Airoha AB157x. Its vendor HID interface
(usage page 0xFF13) carries Airoha's RACE protocol, the same one documented by
the 2025 Airoha headphone research. Framing:

  report 0x06 OUT, 61 data bytes -> b"\x06" + u16le(n) + race_bytes, NUL padded
  report 0x07 IN,  61 data bytes <- GET_REPORT(7): [0]=0x07, [1:3]=u16le n,
                                    [3:3+n] = the next slice of a byte stream

  race frame = 0x05 | type | u16le length | u16le opcode | payload
               length counts the 2-byte opcode plus the payload

Two things make a naive reader fail. Frames do not align to report boundaries,
so the input is a stream that must be reassembled. And the firmware emits its
own debug log as indications on opcode 0x0F92, which has to be filtered out.

No third-party modules: this talks to /dev/hidrawN directly.
"""
import errno
import fcntl
import glob
import os
import struct
import time

VID, PID = 0x34F0, 0x5310
RPT_OUT, RPT_IN, RPT_SIZE = 0x06, 0x07, 62

HEAD = 0x05
T_REQ, T_RESP, T_REQ_NR, T_IND = 0x5A, 0x5B, 0x5C, 0x5D
TYPE_NAMES = {T_REQ: "CMD", T_RESP: "RESP", T_REQ_NR: "CMD_NR", T_IND: "IND"}

# Opcodes. The first three are documented by the Airoha research; the battery
# one comes from the HyperHeadset project, which reads it over BLE.
OP_SDK_VERSION = 0x0301
OP_BD_ADDRESS = 0x0CD5
OP_BATTERY = 0x0CD6
OP_BUILD_VERSION = 0x1E08
OP_FIRMWARE_LOG = 0x0F92          # continuous debug text; always discarded

# Ranges that erase flash or drive a firmware update. Never probed.
DANGEROUS_RANGES = ((0x0400, 0x04FF), (0x1C00, 0x1CFF))


def _ioc(direction, type_, nr, size):
    return (direction << 30) | (size << 16) | (type_ << 8) | nr


def _hidiocginput(size):
    """HIDIOCGINPUT(size) - read/write ioctl, type 'H', nr 0x0A."""
    return _ioc(3, ord("H"), 0x0A, size)


def find_node():
    """Path of the hidraw node backed by the dongle's vendor interface."""
    want = f"HID_ID=0003:{VID:08X}:{PID:08X}"
    for sysfs in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(sysfs, "device", "uevent")) as fh:
                if want in fh.read():
                    return "/dev/" + os.path.basename(sysfs)
        except OSError:
            continue
    return None


class DeviceGone(Exception):
    """The dongle vanished. It re-enumerates when the headset powers off."""


class Frame:
    __slots__ = ("type", "opcode", "payload", "at")

    def __init__(self, type_, opcode, payload):
        self.type = type_
        self.opcode = opcode
        self.payload = payload
        self.at = time.time()

    @property
    def type_name(self):
        return TYPE_NAMES.get(self.type, f"{self.type:#04x}")

    def __str__(self):
        text = "".join(chr(c) if 32 <= c < 127 else "." for c in self.payload)
        return (f"{self.type_name:<6} {self.opcode:#06x} "
                f"{self.payload.hex(' ') or '-'}  |{text}|")


class Race:
    """A RACE session on one hidraw node, with stream reassembly."""

    def __init__(self, node):
        self.node = node
        self.fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
        self._buf = bytearray()
        # Called for every decoded frame, on every code path. The backlog that
        # builds up while nobody is listening carries real events, so no path
        # may throw frames away before this has seen them.
        self.on_frame = None

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _pull(self):
        """One GET_REPORT(7). True if it yielded stream bytes."""
        report = bytearray(RPT_SIZE)
        report[0] = RPT_IN
        try:
            fcntl.ioctl(self.fd, _hidiocginput(RPT_SIZE), report, True)
        except OSError as exc:
            if exc.errno in (errno.ENODEV, errno.ENXIO, errno.ESHUTDOWN, errno.EIO):
                raise DeviceGone(str(exc)) from exc
            return False
        if report[0] != RPT_IN:
            return False
        count = struct.unpack("<H", bytes(report[1:3]))[0]
        if not 0 < count <= RPT_SIZE - 3:
            return False
        self._buf += report[3:3 + count]
        return True

    def _decode(self):
        """Every complete frame currently buffered, log frames dropped."""
        frames = []
        while True:
            if not self._buf:
                break
            if self._buf[0] != HEAD:
                nxt = self._buf.find(bytes([HEAD]), 1)
                if nxt < 0:
                    self._buf.clear()
                    break
                del self._buf[:nxt]
                continue
            if len(self._buf) < 6:
                break
            type_, length, opcode = struct.unpack("<BHH", bytes(self._buf[1:6]))
            payload_len = max(0, length - 2)
            if len(self._buf) < 6 + payload_len:
                break                                  # rest is still in flight
            if type_ not in TYPE_NAMES:                 # false head byte
                del self._buf[:1]
                continue
            payload = bytes(self._buf[6:6 + payload_len])
            del self._buf[:6 + payload_len]
            if opcode != OP_FIRMWARE_LOG:
                frame = Frame(type_, opcode, payload)
                if self.on_frame is not None:
                    self.on_frame(frame)
                frames.append(frame)
        return frames

    def poll(self, seconds=0.0):
        """Read for a window, returning whatever frames arrived."""
        frames, deadline = [], time.time() + seconds
        while True:
            if self._pull():
                frames.extend(self._decode())
            elif time.time() >= deadline:
                break
            else:
                time.sleep(0.02)
            if seconds <= 0:
                break
        return frames

    def drain(self, seconds=0.5):
        """Consume the backlog so a following request sees a clean channel.

        The frames are decoded (and delivered to on_frame) before the leftover
        partial bytes are dropped. Discarding them outright would lose the
        link-up burst, which is the only place some events ever appear.
        """
        deadline = time.time() + seconds
        while time.time() < deadline:
            if not self._pull():
                break
        frames = self._decode()
        self._buf.clear()
        return frames

    def send(self, opcode, payload=b"", type_=T_REQ):
        frame = struct.pack("<BBHH", HEAD, type_, len(payload) + 2, opcode) + payload
        report = bytes([RPT_OUT]) + struct.pack("<H", len(frame)) + frame
        try:
            os.write(self.fd, report.ljust(RPT_SIZE, b"\x00"))
        except OSError as exc:
            if exc.errno in (errno.ENODEV, errno.ENXIO, errno.ESHUTDOWN, errno.EIO):
                raise DeviceGone(str(exc)) from exc
            raise

    def request(self, opcode, payload=b"", timeout=1.5, want=None):
        """Send one request and collect the frames that echo its opcode."""
        self.drain(0.3)
        self.send(opcode, payload)
        got, deadline = [], time.time() + timeout
        while time.time() < deadline:
            for frame in self.poll(0.05):
                if frame.opcode == opcode:
                    got.append(frame)
                    if want is not None and frame.type == want:
                        return got
        return got

    def identify(self):
        """Firmware identity strings, handy for confirming the link works."""
        out = {}
        for key, opcode in (("sdk", OP_SDK_VERSION),
                            ("build", OP_BUILD_VERSION),
                            ("bdaddr", OP_BD_ADDRESS)):
            frames = self.request(opcode, want=T_RESP)
            for frame in frames:
                if frame.type != T_RESP:
                    continue
                if key == "bdaddr" and len(frame.payload) >= 8:
                    # status, earbud selector, then the address little-endian
                    addr = frame.payload[2:8][::-1]
                    out[key] = ":".join(f"{b:02x}" for b in addr)
                else:
                    out[key] = "".join(
                        chr(c) for c in frame.payload if 32 <= c < 127
                    ).strip()
        return out
