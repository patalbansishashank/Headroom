#!/usr/bin/env python3
"""
Headroom CLI: read what the daemon published, or talk to the dongle directly.

  headroomctl.py              last known battery level, plain text
  headroomctl.py --json       one JSON object (Noctalia CustomButton shape)
  headroomctl.py --identify   firmware identity, straight from the dongle
  headroomctl.py --watch      follow the state file as it changes
  headroomctl.py --frames     tail the daemon's frame log
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from headroom_race import DeviceGone, Race, find_node  # noqa: E402

STALE_AFTER = 6 * 60 * 60          # a level older than this is not worth showing


def state_path():
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return os.path.join(base, "headroom", "state.json")


def read_state():
    try:
        with open(state_path()) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def icon_for(percent):
    if percent >= 80:
        return "battery-full"
    if percent >= 40:
        return "battery-medium"
    if percent >= 15:
        return "battery-low"
    return "battery-alert"


def describe(state):
    """(text, tooltip, icon, color) for the current state."""
    if state is None:
        return "", "Headroom daemon is not running", "battery-alert", "none"
    if not state.get("dongle"):
        return "", "PLYR 720 dongle not connected", "battery-alert", "none"
    percent = state.get("percent")
    if percent is None:
        return "", "PLYR 720 linked, battery not reported yet", "battery-medium", "none"
    age = state.get("age") or 0
    if age > STALE_AFTER:
        return "", f"PLYR 720 last reported {percent}% (stale)", "battery-medium", "none"
    when = time.strftime("%H:%M", time.localtime(state.get("updated", time.time())))
    suffix = "" if age < 120 else f", as of {when}"
    return (f"{percent}%",
            f"Crusher PLYR 720: {percent}%{suffix}",
            icon_for(percent),
            "error" if percent < 15 else "none")


def emit(state, as_json):
    text, tooltip, icon, color = describe(state)
    if as_json:
        print(json.dumps({"text": text, "tooltip": tooltip,
                          "icon": icon, "color": color}), flush=True)
    else:
        print(tooltip if not text else f"{text}  ({tooltip})", flush=True)
    return bool(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    parser.add_argument("--watch", action="store_true", help="re-emit as the state changes")
    parser.add_argument("--identify", action="store_true",
                        help="query firmware identity from the dongle")
    parser.add_argument("--frames", action="store_true", help="tail the frame log")
    args = parser.parse_args()

    if args.identify:
        node = find_node()
        if not node:
            sys.exit("dongle not found")
        try:
            with Race(node) as race:
                race.drain(0.6)
                for key, value in race.identify().items():
                    print(f"{key:<7} {value}")
        except PermissionError:
            sys.exit(f"no access to {node}; install udev/70-skullcandy-plyr.rules")
        except DeviceGone:
            sys.exit("dongle went away mid-query")
        return 0

    if args.frames:
        log = os.path.join(os.path.dirname(state_path()), "frames.log")
        if not os.path.exists(log):
            sys.exit("no frame log yet; is headroomd running?")
        os.execvp("tail", ["tail", "-n", "40", "-f", log])

    if args.watch:
        last = None
        while True:
            state = read_state()
            key = None if state is None else (state.get("percent"), state.get("dongle"))
            if key != last:
                emit(state, args.json)
                last = key
            time.sleep(2)

    return 0 if emit(read_state(), args.json) else 1


if __name__ == "__main__":
    sys.exit(main())
