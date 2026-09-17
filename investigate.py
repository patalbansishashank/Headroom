#!/usr/bin/env python3
"""
Find which opcode actually carries the battery, by elimination.

The previous guess failed because a value that merely looked plausible was
accepted without testing. This walks through deliberate actions and records
exactly which opcodes react to each one. A control you can move is not a
battery; a battery is the thing that stays put while you move everything else,
and that reads near full on a freshly charged headset.

Takes the device lock so the plugin's own daemon backs off while this runs,
then releases it. Nothing is written to the device: this only listens.
"""
import fcntl
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin"))
from headroom_race import DeviceGone, Race, find_node  # noqa: E402

STEPS = [
    ("baseline",
     "Do nothing at all. Just leave the headset alone.",
     15),
    ("bass-slider",
     "Move the haptic bass slider slowly all the way up, then all the way down.",
     0),
    ("settle-1",
     "Leave it alone again.",
     10),
    ("volume",
     "Press volume UP about five times, then volume DOWN about five times.",
     0),
    ("settle-2",
     "Leave it alone again.",
     10),
    ("power-cycle",
     "Power the headset OFF, wait a few seconds, then power it back ON.",
     25),
    ("charger-on",
     "Plug the charging cable in (skip with Enter if not handy).",
     15),
    ("charger-off",
     "Unplug the charging cable (skip with Enter if you skipped the last one).",
     15),
]


def take_lock(directory):
    path = os.path.join(directory, "daemon.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    for attempt in range(40):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if attempt == 0:
                print("waiting for the plugin's daemon to release the device ...")
                for pid in os.listdir("/proc"):
                    if not pid.isdigit():
                        continue
                    try:
                        cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="ignore")
                    except OSError:
                        continue
                    if "headroomd.py" in cmd and "python" in cmd:
                        try:
                            os.kill(int(pid), 15)
                        except OSError:
                            pass
            time.sleep(0.5)
    return None


def main():
    directory = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "headroom")
    os.makedirs(directory, exist_ok=True)
    lock = take_lock(directory)
    if lock is None:
        sys.exit("could not take the device lock; is another copy running?")

    node = find_node()
    if not node:
        sys.exit("dongle not found")

    out_path = os.path.join(directory, "investigation.log")
    out = open(out_path, "w")
    dev = Race(node)
    captured = []

    def record(step, frame):
        captured.append((step, frame.opcode, tuple(frame.payload)))
        out.write(f"{step}\t{frame.opcode:#06x}\t{frame.payload.hex(' ')}\n")
        out.flush()

    print(f"\nRecording to {out_path}\n" + "=" * 64)
    try:
        for name, instruction, settle in STEPS:
            print(f"\n[{name}]  {instruction}")
            input("      press Enter when you have done it (or to skip) ... ")
            dev.on_frame = lambda f, s=name: record(s, f)
            deadline = time.time() + max(settle, 3)
            seen = 0
            while time.time() < deadline:
                before = len(captured)
                try:
                    if not dev.pump():
                        time.sleep(0.05)
                except DeviceGone:
                    print("      (dongle re-enumerated; reattaching)")
                    dev.close()
                    time.sleep(1.0)
                    node2 = find_node()
                    while not node2:
                        time.sleep(0.5)
                        node2 = find_node()
                    dev = Race(node2)
                    dev.on_frame = lambda f, s=name: record(s, f)
                    deadline = max(deadline, time.time() + 8)
                seen += len(captured) - before
            print(f"      captured {seen} frame(s)")
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        dev.close()
        out.close()
        os.close(lock)

    # Summary: which opcodes moved during which step, and how much.
    print("\n" + "=" * 64)
    print("opcode behaviour per step (value = first payload byte after status)\n")
    by_op = {}
    for step, op, payload in captured:
        by_op.setdefault(op, {}).setdefault(step, []).append(payload)

    for op in sorted(by_op):
        print(f"  {op:#06x}")
        for step, _, _ in STEPS:
            vals = by_op[op].get(step)
            if not vals:
                continue
            firsts = [v[0] if len(v) == 1 else (v[1] if len(v) > 1 else None) for v in vals]
            firsts = [f for f in firsts if f is not None]
            uniq = sorted(set(firsts))
            spread = f"{min(uniq)}..{max(uniq)}" if len(uniq) > 1 else str(uniq[0]) if uniq else "-"
            print(f"      {step:<14} {len(vals):>3} frames   values {spread}")
        print()

    print("Reading the result:")
    print("  - an opcode that moves during bass-slider or volume is a CONTROL, not a battery")
    print("  - an opcode that stays put through those and reads near 100 on a")
    print("    freshly charged headset is the battery candidate")
    print("  - an opcode that only changes on charger-on/off is charging state")
    print(f"\nFull log: {out_path}")
    print("Restart the plugin's daemon by toggling Headroom off and on in Noctalia,")
    print("or just restart the shell.")


if __name__ == "__main__":
    sys.exit(main())
