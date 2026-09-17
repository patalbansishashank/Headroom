#!/usr/bin/env python3
"""
Log absolutely everything the dongle says, for as long as you leave it running.

This exists because every earlier capture had blind spots:

  - the firmware's own debug log (opcode 0x0F92) was filtered out everywhere,
    and it is the one channel that carries human-readable text
  - bytes dropped while resynchronising to a frame boundary were discarded
    silently, so anything that is not well-formed RACE was invisible
  - captures were seconds long, while a battery moves over hours

So: raw report bytes are logged before any parsing, every decoded frame is
logged including the debug channel, and it runs until stopped.

The point is correlation. Leave it running, note the wall-clock time when the
battery changes on another device, then look at what this recorded around that
moment. Nothing is written to the dongle; this only listens.

    ./listen.py                 # log to the default path, run until Ctrl-C
    ./listen.py -o /tmp/x.log   # somewhere else
"""
import argparse
import fcntl
import os
import signal
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin"))
from headroom_race import (  # noqa: E402
    DeviceGone, HEAD, RPT_IN, RPT_SIZE, TYPE_NAMES, Race, find_node,
)

STOP = False
MAX_BYTES = 64 * 1024 * 1024


def _stop(_s, _f):
    global STOP
    STOP = True


def take_lock(directory):
    """Hold the device lock so the plugin's daemon stands down while we run."""
    fd = os.open(os.path.join(directory, "daemon.lock"), os.O_CREAT | os.O_RDWR, 0o644)
    for attempt in range(60):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if attempt == 0:
                print("waiting for the plugin daemon to release the device ...")
                for pid in os.listdir("/proc"):
                    if not pid.isdigit():
                        continue
                    try:
                        cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="ignore")
                    except OSError:
                        continue
                    if "headroomd.py" in cmd and "python" in cmd:
                        try:
                            os.kill(int(pid), signal.SIGTERM)
                        except OSError:
                            pass
            time.sleep(0.5)
    return None


class Log:
    def __init__(self, path):
        self.path = path
        self.fh = open(path, "a", buffering=1)
        self.counts = {}

    def write(self, kind, text):
        self.counts[kind] = self.counts.get(kind, 0) + 1
        now = time.time()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        self.fh.write(f"{stamp}.{int((now % 1) * 1000):03d}  {kind:<6} {text}\n")
        if self.fh.tell() > MAX_BYTES:
            self.fh.close()
            os.replace(self.path, self.path + ".1")
            self.fh = open(self.path, "a", buffering=1)


def printable(payload):
    return "".join(chr(c) if 32 <= c < 127 else "." for c in payload)


class Capture(Race):
    """Race, but nothing is filtered and raw bytes are recorded first."""

    def __init__(self, node, log):
        super().__init__(node)
        self.log = log

    def _pull(self):
        report = bytearray(RPT_SIZE)
        report[0] = RPT_IN
        try:
            fcntl.ioctl(self.fd, self._ginput, report, True)
        except OSError as exc:
            import errno
            if exc.errno in (errno.ENODEV, errno.ENXIO, errno.ESHUTDOWN, errno.EIO):
                raise DeviceGone(str(exc)) from exc
            return False
        if report[0] != RPT_IN:
            return False
        count = struct.unpack("<H", bytes(report[1:3]))[0]
        if not 0 < count <= RPT_SIZE - 3:
            return False
        chunk = bytes(report[3:3 + count])
        self.log.write("RAW", chunk.hex(" "))
        self._buf += chunk
        return True

    def decode_all(self):
        """Parse frames, logging the debug channel and any dropped bytes too."""
        while True:
            if not self._buf:
                return
            if self._buf[0] != HEAD:
                nxt = self._buf.find(bytes([HEAD]), 1)
                dropped = bytes(self._buf[:nxt if nxt > 0 else len(self._buf)])
                if dropped:
                    self.log.write("DROP", dropped.hex(" ") + f"  |{printable(dropped)}|")
                if nxt < 0:
                    self._buf.clear()
                    return
                del self._buf[:nxt]
                continue
            if len(self._buf) < 6:
                return
            type_, length, opcode = struct.unpack("<BHH", bytes(self._buf[1:6]))
            payload_len = max(0, length - 2)
            if len(self._buf) < 6 + payload_len:
                return
            if type_ not in TYPE_NAMES:
                self.log.write("DROP", f"{self._buf[0]:02x}  (bad type {type_:#04x})")
                del self._buf[:1]
                continue
            payload = bytes(self._buf[6:6 + payload_len])
            del self._buf[:6 + payload_len]
            name = TYPE_NAMES[type_]
            if opcode == 0x0F92:
                # The firmware's own log. Previously discarded everywhere,
                # which is exactly why it is worth reading now.
                self.log.write("FWLOG", printable(payload).rstrip("."))
            else:
                self.log.write("FRAME", f"{name:<6} {opcode:#06x} "
                                        f"{payload.hex(' ') or '-'}  |{printable(payload)}|")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    default = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "headroom", "listen.log")
    ap.add_argument("-o", "--out", default=default, help=f"log path (default {default})")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    directory = os.path.dirname(args.out)
    os.makedirs(directory, exist_ok=True)
    lock = take_lock(os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "headroom"))
    if lock is None:
        sys.exit("could not take the device lock")

    log = Log(args.out)
    log.write("START", f"listening, pid {os.getpid()}")
    print(f"logging everything to {args.out}")
    print("leave this running; note the time when the battery changes elsewhere")

    dev = None
    delay = 0.5
    while not STOP:
        if dev is None:
            node = find_node()
            if not node:
                time.sleep(1.0)
                continue
            try:
                dev = Capture(node, log)
                dev._ginput = _ginput_for(RPT_SIZE)
                log.write("ATTACH", node)
            except OSError:
                time.sleep(1.0)
                continue
        try:
            if dev._pull():
                dev.decode_all()
                delay = 0.005
            else:
                delay = min(delay * 2.0, 0.5)
            time.sleep(delay)
        except DeviceGone as exc:
            log.write("DETACH", str(exc))
            dev.close()
            dev = None
            time.sleep(1.0)

    if dev:
        dev.close()
    log.write("STOP", "counts: " + ", ".join(f"{k}={v}" for k, v in sorted(log.counts.items())))
    os.close(lock)
    print("\nstopped. counts:", log.counts)


def _ginput_for(size):
    return (3 << 30) | (size << 16) | (ord("H") << 8) | 0x0A


if __name__ == "__main__":
    sys.exit(main())
