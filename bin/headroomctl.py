#!/usr/bin/env python3
"""
Headroom CLI: read what the daemon published, or query a device directly.

  headroomctl.py              one line per device
  headroomctl.py --json       the raw state, pretty-printed
  headroomctl.py --watch      re-print as things change
  headroomctl.py --probe      bypass the daemon and ask each device now
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STALE_AFTER = 6 * 60 * 60


def state_path():
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "headroom", "state.json")


def read_state():
    try:
        with open(state_path()) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def describe(device):
    percent = device.get("percent")
    name = device.get("name", device.get("id", "device"))
    if percent is None:
        return f"{name:<20} {'--':>5}   " + ("connected, no level yet"
                                             if device.get("present") else "not connected")
    updated = device.get("updated")
    age = 0 if updated is None else max(0.0, time.time() - updated)
    bits = []
    if device.get("charging"):
        bits.append("charging")
    if not device.get("present"):
        bits.append("disconnected")
    if age > 120 and updated:
        bits.append("at " + time.strftime("%H:%M", time.localtime(updated)))
    if age > STALE_AFTER:
        bits.append("stale")
    return f"{name:<20} {str(percent) + '%':>5}   " + ", ".join(bits)


def show(state):
    if state is None:
        print("headroom daemon is not running", file=sys.stderr)
        return False
    devices = state.get("devices", [])
    if not devices:
        print("no devices")
        return False
    for device in devices:
        print(describe(device))
    return any(d.get("percent") is not None for d in devices)


def probe():
    """Ask each source directly, ignoring the daemon and its state file."""
    import headroom_sources as sources
    for cls in sources.SOURCES:
        source = cls()
        try:
            present = source.available()
        except Exception as exc:
            print(f"{source.id:<20} error: {exc}")
            continue
        if not present:
            print(f"{source.name:<20} not present")
            continue
        if isinstance(source, sources.PolledSource):
            try:
                reading = source.read()
            except Exception as exc:
                print(f"{source.name:<20} error: {exc}")
                continue
            print(f"{source.name:<20} "
                  + (f"{reading.percent}%" if reading.percent is not None else "no answer"))
        elif hasattr(source, "_read_now"):        # pushed, but can be asked
            reading = source._read_now()
            print(f"{source.name:<20} "
                  + (f"{reading.percent}%" if reading else "no answer (asleep?)"))
        else:
            print(f"{source.name:<20} present; reports only when it chooses")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print the raw state")
    parser.add_argument("--watch", action="store_true", help="re-print as things change")
    parser.add_argument("--probe", action="store_true", help="ask each device directly")
    args = parser.parse_args()

    if args.probe:
        probe()
        return 0

    if args.json:
        state = read_state()
        print(json.dumps(state or {}, indent=2))
        return 0 if state else 1

    if args.watch:
        last = None
        try:
            while True:
                state = read_state()
                key = json.dumps(state.get("devices") if state else None, sort_keys=True)
                if key != last:
                    print(time.strftime("[%H:%M:%S]"))
                    show(state)
                    last = key
                time.sleep(2)
        except KeyboardInterrupt:
            return 130

    return 0 if show(read_state()) else 1


if __name__ == "__main__":
    sys.exit(main())
