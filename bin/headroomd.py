#!/usr/bin/env python3
"""
Headroom daemon: collect battery levels from every known source and publish
them as one small JSON state file.

Sources are declared in headroom_sources.py; this file does not know what a
headset or a mouse is. Two shapes are supported, because the hardware differs
in kind: polled sources are asked on a timer, pushed sources hold a connection
open and speak when they choose. Each runs on its own thread so a slow or
sleeping device cannot stall the others.

Design notes that matter for cost, since this runs all day:

  - the state file is written only when a value actually changes; readers
    derive age from the stored timestamp, so a still-correct file is never
    rewritten
  - polled sources are read every couple of minutes, not continuously; a
    battery does not move faster than that
  - the process asks the kernel to kill it when its parent dies, so restarting
    the shell cannot leave an orphan holding a device
"""
import argparse
import ctypes
import fcntl
import json
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from headroom_sources import PolledSource, PushedSource, Reading, SOURCES  # noqa: E402

STOP = threading.Event()
EXIT_OK = 0
EXIT_ALREADY_RUNNING = 3
PR_SET_PDEATHSIG = 1


def _stop(_signum, _frame):
    STOP.set()


def die_with_parent():
    """Be killed when whoever started us goes away.

    Noctalia runs this as a child. A child outlives its parent by default, so
    without this a shell restart leaves an orphan holding the device locks and
    the new daemon can never start.
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    except (OSError, AttributeError):
        return
    if os.getppid() == 1:
        sys.exit(EXIT_OK)


def state_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(os.path.expanduser("~"), ".cache")
    path = os.path.join(base, "headroom")
    os.makedirs(path, exist_ok=True)
    return path


def acquire_lock(directory):
    """Single-instance guard; the descriptor must stay open for our lifetime."""
    fd = os.open(os.path.join(directory, "daemon.lock"), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


class Publisher:
    """Owns the state file. Writes are atomic and only happen on change."""

    def __init__(self, directory, emit_lines=False):
        self.path = os.path.join(directory, "state.json")
        self.emit_lines = emit_lines
        self._lock = threading.Lock()
        self._readings = {}          # source id -> Reading
        self._meta = {}              # source id -> static description
        self._last_written = None
        self._restore()

    def describe(self, source):
        self._meta[source.id] = {
            "id": source.id,
            "name": source.name,
            "icon": source.icon,
            "note": source.staleness_note,
        }

    def _restore(self):
        """Carry levels across a restart.

        A pushed source may not speak again for hours, so starting blank would
        leave the bar empty for no good reason. Timestamps are kept as they
        were, so an old value still presents as old.
        """
        try:
            with open(self.path) as fh:
                previous = json.load(fh)
        except (OSError, ValueError):
            return
        for entry in previous.get("devices", []):
            percent, when = entry.get("percent"), entry.get("updated")
            if isinstance(percent, int) and 0 < percent <= 100 and isinstance(when, (int, float)):
                self._readings[entry.get("id")] = Reading(
                    percent=percent, charging=bool(entry.get("charging")),
                    present=False, at=when)

    def update(self, source_id, reading):
        with self._lock:
            previous = self._readings.get(source_id)
            # A source that cannot reach its device reports presence without a
            # level. Keep the last known number rather than blanking it.
            if reading.percent is None and previous is not None:
                reading.percent = previous.percent
                reading.charging = previous.charging
                reading.at = previous.at
            self._readings[source_id] = reading
            self._publish_locked()

    def _snapshot(self):
        devices = []
        for source_id, meta in self._meta.items():
            reading = self._readings.get(source_id)
            devices.append({
                **meta,
                "percent": reading.percent if reading else None,
                "charging": bool(reading.charging) if reading else False,
                "present": bool(reading.present) if reading else False,
                "updated": reading.at if reading and reading.percent is not None else None,
            })
        return devices

    def _publish_locked(self):
        devices = self._snapshot()
        signature = json.dumps(devices, sort_keys=True)
        if signature == self._last_written:
            return
        self._last_written = signature

        payload = {"devices": devices, "published": time.time()}
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self.path)
        except OSError:
            pass
        if self.emit_lines:
            print(json.dumps({"devices": devices}), flush=True)

    def publish(self):
        with self._lock:
            self._publish_locked()


def run_polled(source, pub, verbose):
    """Ask a source on its own schedule until we are told to stop."""
    while not STOP.is_set():
        try:
            reading = source.read()
        except Exception as exc:                      # never kill the thread
            if verbose:
                print(f"headroomd: {source.id} read failed: {exc}", flush=True)
            reading = Reading(present=False)
        pub.update(source.id, reading)
        if verbose and reading.percent is not None:
            print(f"headroomd: {source.id} {reading.percent}%"
                  f"{' charging' if reading.charging else ''}", flush=True)
        STOP.wait(source.interval)


def run_pushed(source, pub, verbose):
    """Let a source hold its own connection and call back."""
    def emit(reading):
        pub.update(source.id, reading)
        if verbose and reading.percent is not None:
            print(f"headroomd: {source.id} {reading.percent}%", flush=True)
    try:
        source.run(emit, STOP.is_set)
    except Exception as exc:
        if verbose:
            print(f"headroomd: {source.id} stopped: {exc}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log every reading as it arrives")
    parser.add_argument("--emit", action="store_true",
                        help="print a JSON line on stdout whenever the state changes")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    die_with_parent()

    directory = state_dir()
    if acquire_lock(directory) is None:
        print("headroomd: another instance is already running; exiting", file=sys.stderr)
        return EXIT_ALREADY_RUNNING

    pub = Publisher(directory, emit_lines=args.emit)

    threads = []
    for cls in SOURCES:
        source = cls()
        try:
            present = source.available()
        except Exception:
            present = False
        pub.describe(source)
        if not present:
            if args.verbose:
                print(f"headroomd: {source.id} not present", flush=True)
            continue
        target = run_polled if isinstance(source, PolledSource) else run_pushed
        thread = threading.Thread(target=target, args=(source, pub, args.verbose),
                                  name=source.id, daemon=True)
        thread.start()
        threads.append(thread)
        if args.verbose:
            kind = "polled" if isinstance(source, PolledSource) else "pushed"
            print(f"headroomd: {source.id} started ({kind})", flush=True)

    pub.publish()
    if not threads:
        print("headroomd: no sources present", file=sys.stderr)

    while not STOP.is_set():
        STOP.wait(1.0)
    for thread in threads:
        thread.join(timeout=2.0)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
