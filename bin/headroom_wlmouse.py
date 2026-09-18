#!/usr/bin/env python3
"""
Battery for WLmouse receivers (Sword X and siblings), over hidraw.

These use the COMPX page/command protocol, the same framing Lamzu receivers
use. It is carried on a 64-byte FEATURE report at report id 0, on the
vendor-page interface (usage page 0xFFFF), not on the mouse or keyboard
interfaces.

  request  [status=0x00][0][target][length][page][command][args...]  64 bytes
  reply    same shape, status 0xA1 on success, payload from byte 6

The reply may be shifted one byte, so both offsets are checked. A sleeping
mouse simply does not answer until it wakes, hence the retries.

Protocol reference: the OpenMouse project's WLmouse driver
(github.com/OpenMouse-Project/mouse-protocol). No third-party modules here.
"""
import errno
import fcntl
import glob
import os
import struct
import time

VENDOR_ID = 0x36A7
VENDOR_USAGE_PAGE = 0xFFFF

PACKET_LENGTH = 64
HEADER_LENGTH = 6
REPORT_ID = 0

STATUS_REQUEST = 0x00
STATUS_OK = 0xA1
STATUS_UNSUPPORTED = 0xA2

TARGET_MOUSE = 0x02
PAGE_DEVICE = 0x00
COMMAND_BATTERY = 0x83
BATTERY_LENGTH = 0x02

FRAME_OFFSETS = (0, 1)          # some replies arrive shifted by one byte
RESPONSE_DELAY = 0.03
WAKE_DELAY = 0.30
QUICK_ATTEMPTS = 3


def _ioc(direction, type_, nr, size):
    return (direction << 30) | (size << 16) | (type_ << 8) | nr


def _set_feature(size):
    return _ioc(3, ord("H"), 0x06, size)


def _get_feature(size):
    return _ioc(3, ord("H"), 0x07, size)


def _usage_pages(descriptor):
    """Every usage page mentioned by a report descriptor."""
    pages, i = set(), 0
    while i < len(descriptor):
        prefix = descriptor[i]
        size = prefix & 0x03
        size = 4 if size == 3 else size
        if (prefix & 0xFC) == 0x04:
            pages.add(int.from_bytes(descriptor[i + 1:i + 1 + size], "little"))
        i += 1 + size
    return pages


def _has_config_report(descriptor):
    """True if this descriptor declares the 64-byte vendor feature report.

    Several interfaces of the same receiver mention the vendor usage page, so
    matching on the page alone finds the same physical mouse more than once.
    The config interface is the one with a 64-item feature report.
    """
    i, count, in_vendor = 0, 0, False
    while i < len(descriptor):
        prefix = descriptor[i]
        size = prefix & 0x03
        size = 4 if size == 3 else size
        tag = prefix & 0xFC
        value = int.from_bytes(descriptor[i + 1:i + 1 + size], "little") if size else 0
        if tag == 0x04:
            in_vendor = value == VENDOR_USAGE_PAGE
        elif tag == 0x94:
            count = value
        elif tag == 0xB0 and in_vendor and count == PACKET_LENGTH:
            return True
        i += 1 + size
    return False


def find_nodes():
    """hidraw nodes for WLmouse receivers that expose the config interface."""
    found, seen = [], set()
    for sysfs in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            uevent = open(os.path.join(sysfs, "device", "uevent")).read()
        except OSError:
            continue
        if f":{VENDOR_ID:08X}:" not in uevent:
            continue
        try:
            descriptor = open(os.path.join(sysfs, "device", "report_descriptor"), "rb").read()
        except OSError:
            continue
        # The config interface is the vendor-page one; the mouse and keyboard
        # interfaces of the same device will not answer.
        if not _has_config_report(descriptor):
            continue
        name, phys = "WLmouse", ""
        for line in uevent.splitlines():
            if line.startswith("HID_NAME="):
                name = line.split("=", 1)[1].strip()
            elif line.startswith("HID_PHYS="):
                phys = line.split("=", 1)[1].strip().split("/input")[0]
        if phys and phys in seen:           # one entry per physical receiver
            continue
        seen.add(phys)
        found.append(("/dev/" + os.path.basename(sysfs), name, phys))
    return found


def find_input_node(phys):
    """The plain mouse-input node of the same receiver.

    Used to tell whether the mouse is awake. Config requests go unanswered
    while it sleeps, and waiting on its input costs nothing until it moves.
    """
    for sysfs in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            uevent = open(os.path.join(sysfs, "device", "uevent")).read()
        except OSError:
            continue
        if f":{VENDOR_ID:08X}:" not in uevent:
            continue
        this_phys = ""
        for line in uevent.splitlines():
            if line.startswith("HID_PHYS="):
                this_phys = line.split("=", 1)[1].strip().split("/input")[0]
        if phys and this_phys != phys:
            continue
        try:
            descriptor = open(os.path.join(sysfs, "device", "report_descriptor"), "rb").read()
        except OSError:
            continue
        # Generic Desktop, and not the vendor config interface.
        if 0x0001 in _usage_pages(descriptor) and not _has_config_report(descriptor):
            return "/dev/" + os.path.basename(sysfs)
    return None


class Unreachable(Exception):
    """The receiver is gone, or the mouse never answered."""


def _encode(target, length, page, command, args=()):
    packet = bytearray(PACKET_LENGTH)
    packet[0] = STATUS_REQUEST
    packet[2] = target
    packet[3] = length
    packet[4] = page
    packet[5] = command
    packet[HEADER_LENGTH:HEADER_LENGTH + len(args)] = bytes(args)
    return bytes(packet)


def _exchange(fd, target, length, page, command, args=(), attempts=12):
    request = _encode(target, length, page, command, args)
    for attempt in range(attempts):
        out = bytearray(bytes([REPORT_ID]) + request)
        try:
            fcntl.ioctl(fd, _set_feature(len(out)), out, True)
        except OSError as exc:
            if exc.errno in (errno.ENODEV, errno.ENXIO, errno.ESHUTDOWN):
                raise Unreachable(str(exc)) from exc
            raise
        time.sleep(RESPONSE_DELAY)

        buf = bytearray(PACKET_LENGTH + 1)
        buf[0] = REPORT_ID
        try:
            fcntl.ioctl(fd, _get_feature(len(buf)), buf, True)
        except OSError as exc:
            if exc.errno in (errno.ENODEV, errno.ENXIO, errno.ESHUTDOWN):
                raise Unreachable(str(exc)) from exc
            raise
        reply = bytes(buf[1:])

        for offset in FRAME_OFFSETS:
            if offset + 5 >= len(reply):
                continue
            if reply[4 + offset] != page or reply[5 + offset] != command:
                continue
            if reply[offset] == STATUS_UNSUPPORTED:
                raise Unreachable(f"page {page:#04x} command {command:#04x} unsupported")
            if reply[offset] != STATUS_OK:
                continue
            start = HEADER_LENGTH + offset
            end = start + min(reply[3 + offset], PACKET_LENGTH - start)
            return reply[start:end]
        time.sleep(RESPONSE_DELAY if attempt < QUICK_ATTEMPTS else WAKE_DELAY)
    raise Unreachable("no answer; the mouse may be asleep or out of range")


def read_battery(node):
    """(percent, charging) for the mouse paired to this receiver."""
    fd = os.open(node, os.O_RDWR)
    try:
        payload = _exchange(fd, TARGET_MOUSE, BATTERY_LENGTH, PAGE_DEVICE, COMMAND_BATTERY)
    finally:
        os.close(fd)
    if len(payload) < 2:
        raise Unreachable(f"short battery payload {payload.hex(' ')}")
    charging = payload[0] == 1
    percent = payload[1]
    if not 0 <= percent <= 100:
        raise Unreachable(f"battery out of range: {percent}")
    return percent, charging


if __name__ == "__main__":
    for node, name, _phys in find_nodes() or []:
        try:
            pct, charging = read_battery(node)
            print(f"{name}  ({node})  {pct}%  {'charging' if charging else 'discharging'}")
        except Unreachable as exc:
            print(f"{name}  ({node})  unavailable: {exc}")
    else:
        if not find_nodes():
            print("no WLmouse receiver found")
