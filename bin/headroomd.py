#!/usr/bin/env python3
"""
Headroom daemon: keep a live RACE session to the Skullcandy dongle and publish
the headset's battery level as a small JSON state file.

Why a daemon rather than a one-shot reader. The dongle refuses an on-demand
battery request over USB: opcode 0x0CD6 is answered with a status-only ack
carrying 0x02, for every argument tried. The level instead arrives unsolicited,
as a 0x5D indication, and appears to be pushed around the moment the headset
links. Nothing polls it into existence, so the only way to catch it is to hold
the channel open and be listening when it comes.

The dongle also re-enumerates when the headset is powered off, so the hidraw
node disappears and comes back under a new device number. The daemon treats
that as normal and reconnects.

Every frame that is not firmware log spam is written to a frame log. The battery
delivery model is not fully pinned down yet, and that log is what will settle it.
"""
import argparse
import ctypes
import fcntl
import json
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from headroom_race import (  # noqa: E402
    DeviceGone, Race, find_node,
)

# The dongle announces the headset link on this opcode. Byte 2 is the link flag
# and bytes 4..9 are the headset's Bluetooth address, little-endian:
#   00 02 00 01 <addr> ff 00   headset gone
#   00 02 01 01 <addr> 80 01   headset linked
OP_LINK_STATE = 0x2CB1

STOP = False
FRAME_LOG_MAX_BYTES = 512 * 1024

EXIT_OK = 0
EXIT_ALREADY_RUNNING = 3      # distinct so the supervisor can back off, not hammer

# Read pacing. The dongle answers only GET_REPORT, so this has to poll; the
# question is how often. It buffers frames, so nothing is lost by asking
# slowly, and a burst is drained at full speed once the first frame shows up.
# Idle cost drops ~50x versus polling flat out.
IDLE_DELAY = 1.0
BUSY_DELAY = 0.005

PR_SET_PDEATHSIG = 1


def _stop(_signum, _frame):
    global STOP
    STOP = True


def die_with_parent():
    """Ask the kernel to kill us when whoever started us goes away.

    Noctalia starts this daemon as a child process, but a child survives its
    parent by default. Restarting the shell therefore leaves an orphan holding
    the device lock, and the new shell's daemon can never acquire it. Without
    this, every shell restart needs a manual cleanup.
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    except (OSError, AttributeError):
        return                       # not Linux, or no prctl: nothing to do
    # The parent may already be gone, in which case the signal above never
    # arrives and we would linger exactly as intended to prevent.
    if os.getppid() == 1:
        sys.exit(EXIT_OK)


def acquire_lock(directory):
    """Single-instance guard.

    Two daemons would fight over both the device and the state file, and the
    loser would publish a "dongle gone" that is merely its own shutdown. Returns
    the held descriptor, which must stay open for the life of the process.
    """
    path = os.path.join(directory, "daemon.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


def state_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    path = os.path.join(base, "headroom")
    os.makedirs(path, exist_ok=True)
    return path


class Publisher:
    """Owns the state file. Writes are atomic so a reader never sees a tear."""

    def __init__(self, directory):
        self.path = os.path.join(directory, "state.json")
        self.frame_log = os.path.join(directory, "frames.log")
        self.percent = None
        self.percent_at = None
        self._restore()
        self.dongle = False
        self.linked = None
        self.headset_addr = None
        self.identity = {}
        self.emit = False            # also print a JSON line on every change
        self._last_emitted = None
        self._last_written = None

    def _restore(self):
        """Carry the last known level across a restart.

        The dongle only volunteers the battery around the moment the headset
        links. Starting blank would mean showing nothing until the next power
        cycle, which could be hours. The level is reloaded with its original
        timestamp, so it is presented as old rather than as fresh.
        """
        try:
            with open(self.path) as fh:
                previous = json.load(fh)
        except (OSError, ValueError):
            return
        # Deliberately not restoring "percent": every value ever written to it
        # came from an opcode that turned out not to be the battery. Restoring
        # one would resurrect a wrong number across a restart.
        if previous.get("percent") is not None:
            return

    def note_frame(self, frame):
        """Append to the frame log, trimming it when it gets large."""
        try:
            if (os.path.exists(self.frame_log)
                    and os.path.getsize(self.frame_log) > FRAME_LOG_MAX_BYTES):
                with open(self.frame_log) as fh:
                    tail = fh.readlines()[-2000:]
                with open(self.frame_log, "w") as fh:
                    fh.writelines(tail)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(self.frame_log, "a") as fh:
                fh.write(f"{stamp} {frame}\n")
        except OSError:
            pass

    def publish(self, force=False):
        """Write the state file, but only when something actually changed.

        Called unconditionally this would rewrite the file twice a second
        forever with identical content. Consumers derive age from `updated`,
        so a still-current file does not need rewriting just because time
        passed.
        """
        signature = (self.percent, self.percent_at, self.dongle,
                     self.linked, self.headset_addr)
        if not force and signature == self._last_written:
            return
        self._last_written = signature

        payload = {
            "percent": self.percent,
            "updated": self.percent_at,
            "dongle": self.dongle,
            "linked": self.linked,
            "headset": self.headset_addr,
            "identity": self.identity,
            "published": time.time(),
        }
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self.path)
        except OSError:
            pass

        if self.emit:
            # Noctalia reads this on the daemon's stdout. Only changes are
            # printed, so an idle headset does not spam the shell.
            signature = (self.percent, self.dongle, self.linked)
            if signature != self._last_emitted:
                self._last_emitted = signature
                print(json.dumps({"percent": self.percent,
                                  "dongle": self.dongle,
                                  "linked": self.linked,
                                  "updated": self.percent_at}), flush=True)


def handle_frame(pub, frame, verbose):
    """Called for every frame the dongle sends, from whichever read path."""
    pub.note_frame(frame)
    if verbose:
        print(f"  {frame}", flush=True)

    # No battery source is currently known. 0x0CD6 was used here and was wrong;
    # see headroom_race.py. Publishing a number from an unidentified opcode is
    # worse than publishing nothing, because a confident wrong battery level
    # makes people charge a headset that does not need it.
    if frame.opcode == OP_LINK_STATE and len(frame.payload) >= 10:
        pub.linked = bool(frame.payload[2])
        addr = frame.payload[4:10][::-1]
        pub.headset_addr = ":".join(f"{b:02x}" for b in addr)
        if verbose:
            print(f"headroomd: headset {'linked' if pub.linked else 'gone'} "
                  f"({pub.headset_addr})", flush=True)
        pub.publish()


def serve(pub, verbose, poll_interval):
    """One connected session. Returns when the dongle goes away."""
    node = find_node()
    if not node:
        return False

    try:
        race = Race(node)
    except PermissionError:
        print(f"headroomd: no access to {node}. Install the udev rule "
              f"(udev/70-skullcandy-plyr.rules) and re-trigger udev.",
              file=sys.stderr)
        time.sleep(5)
        return False
    except OSError:
        return False

    pub.dongle = True
    if verbose:
        print(f"headroomd: attached to {node}", flush=True)

    race.on_frame = lambda frame: handle_frame(pub, frame, verbose)

    with race:
        try:
            # The link-up burst is already queued by the time we attach. Decode
            # it before anything else; this is where the interesting events are.
            race.drain(1.5)
            try:
                pub.identity = race.identify()
                if verbose and pub.identity:
                    print(f"headroomd: {pub.identity}", flush=True)
            except DeviceGone:
                raise
            except OSError:
                pass
            pub.publish()

            next_poll = None
            delay = IDLE_DELAY
            while not STOP:
                # Frames reach the publisher through the callback. Speed up the
                # moment anything arrives so a link-up burst drains promptly,
                # then ease back off to the idle rate.
                if race.pump():
                    delay = BUSY_DELAY
                else:
                    delay = min(delay * 2.0, IDLE_DELAY)


                time.sleep(delay)
        except DeviceGone:
            if verbose:
                print("headroomd: dongle went away (headset powered off?)", flush=True)
        except OSError:
            pass

    pub.dongle = False
    pub.linked = None
    pub.publish()
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every frame as it arrives")
    parser.add_argument("--poll-interval", type=float, default=0.0, metavar="SEC",
                        help="also send a battery request this often; off by "
                             "default because the dongle refuses it (see README)")
    parser.add_argument("--emit", action="store_true",
                        help="print a JSON line on stdout whenever the state changes")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    die_with_parent()

    directory = state_dir()
    if acquire_lock(directory) is None:
        print("headroomd: another instance already holds the dongle; exiting",
              file=sys.stderr)
        return EXIT_ALREADY_RUNNING

    pub = Publisher(directory)
    pub.emit = args.emit
    pub.publish(force=True)
    if args.verbose:
        print(f"headroomd: state -> {pub.path}", flush=True)

    while not STOP:
        if not serve(pub, args.verbose, args.poll_interval):
            time.sleep(2.0)          # dongle absent or unreadable; wait and retry
    pub.dongle = False
    pub.publish()
    return 0


if __name__ == "__main__":
    sys.exit(main())
