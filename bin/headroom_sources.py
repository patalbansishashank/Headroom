#!/usr/bin/env python3
"""
The set of battery sources Headroom knows about.

Adding a device means adding one Source subclass here and listing it in
SOURCES. Nothing else in the daemon, the CLI or the QML needs to change: the
daemon publishes whatever sources report, and the bar renders one gauge per
entry.

Two kinds of source exist, because the hardware differs in kind:

  PolledSource   answer on demand. The daemon asks every `interval` seconds.
                 The WLmouse receiver works this way.

  PushedSource   speak only when they feel like it. The daemon keeps a
                 connection open and waits. The PLYR 720 dongle works this
                 way: it reports the level when the headset links and at no
                 other time, and refuses to be asked.
"""
import time


class Reading:
    """One device's state at a moment."""

    __slots__ = ("percent", "charging", "present", "at")

    def __init__(self, percent=None, charging=False, present=False, at=None):
        self.percent = percent
        self.charging = charging
        self.present = present
        self.at = at if at is not None else time.time()


class Source:
    """A battery Headroom can show. Subclasses fill in id/name/icon."""

    id = "source"
    name = "Device"
    icon = "battery"
    # Shown in the tooltip when the value is old; some devices genuinely
    # cannot be refreshed on demand and the user should know why.
    staleness_note = ""

    def available(self):
        """True if the hardware is present at all."""
        raise NotImplementedError


class PolledSource(Source):
    interval = 120.0          # seconds between reads; batteries move slowly

    def read(self):
        """Return a Reading, or raise. Called from a worker thread."""
        raise NotImplementedError


class PushedSource(Source):
    """Owns its own connection and calls back when the device speaks."""

    def run(self, emit, should_stop):
        """Block until should_stop(); call emit(Reading) on each update."""
        raise NotImplementedError


# --------------------------------------------------------------------------
# WLmouse receivers (Sword X and siblings)
# --------------------------------------------------------------------------

class WLMouseSource(PushedSource):
    """WLmouse receivers (Sword X and siblings).

    Pushed rather than polled, even though the protocol is request/response.
    The mouse sleeps aggressively and simply does not answer config requests
    while asleep, so polling on a timer mostly burns retries against a device
    that is not listening. Instead this waits on the mouse's own input node,
    which costs nothing until the mouse moves, and reads the battery when the
    mouse is demonstrably awake, at most once per `interval`.
    """

    id = "wlmouse"
    icon = "mouse"
    interval = 120.0          # minimum seconds between reads while in use
    staleness_note = "Refreshes while the mouse is in use"

    def __init__(self):
        self.name = "WLmouse"
        self._node = None
        self._phys = ""

    def _locate(self):
        import headroom_wlmouse as wl
        nodes = wl.find_nodes()
        if not nodes:
            self._node, self._phys = None, ""
            return None
        node, raw_name, phys = nodes[0]
        self._node, self._phys = node, phys
        pretty = raw_name.replace("WL WLMOUSE", "").replace("RECEIVER", "").strip().title()
        self.name = pretty or "WLmouse"
        return node

    def available(self):
        return self._locate() is not None

    def _read_now(self):
        import headroom_wlmouse as wl
        try:
            percent, charging = wl.read_battery(self._node)
        except wl.Unreachable:
            return None
        return Reading(percent=percent, charging=charging, present=True)

    def run(self, emit, should_stop):
        import select
        import os as _os
        import headroom_wlmouse as wl

        last_read = 0.0
        while not should_stop():
            if self._locate() is None:
                emit(Reading(present=False))
                time.sleep(5.0)
                continue
            emit(Reading(present=True))

            # One attempt up front: the mouse may already be awake.
            reading = self._read_now()
            if reading:
                emit(reading)
                last_read = time.time()

            input_node = wl.find_input_node(self._phys)
            if not input_node:
                # No way to detect wake; fall back to a slow timer.
                time.sleep(self.interval)
                continue
            try:
                fd = _os.open(input_node, _os.O_RDONLY | _os.O_NONBLOCK)
            except OSError:
                time.sleep(5.0)
                continue

            poller = select.poll()
            poller.register(fd, select.POLLIN)
            try:
                while not should_stop():
                    if not poller.poll(1000):
                        continue                    # idle: no cost, no reads
                    try:
                        while _os.read(fd, 64):     # drain; we only need the fact
                            pass
                    except BlockingIOError:
                        pass
                    except OSError:
                        break                       # receiver unplugged
                    if time.time() - last_read < self.interval:
                        continue
                    reading = self._read_now()
                    last_read = time.time()
                    if reading:
                        emit(reading)
            finally:
                try:
                    _os.close(fd)
                except OSError:
                    pass


# --------------------------------------------------------------------------
# Skullcandy Crusher PLYR 720, over its 2.4 GHz dongle
# --------------------------------------------------------------------------

class PlyrHeadsetSource(PushedSource):
    id = "plyr720"
    name = "Crusher PLYR 720"
    icon = "headphones"
    staleness_note = "Refreshes when the headset links"

    def available(self):
        import headroom_race as race
        return race.find_node() is not None

    def run(self, emit, should_stop):
        import headroom_race as race
        from headroom_race import BATTERY_BURST_GAP, OP_BATTERY, T_IND

        OP_LINK_STATE = 0x2CB1
        burst, last_frame_at, linked = [], 0.0, None

        while not should_stop():
            node = race.find_node()
            if not node:
                emit(Reading(present=False))
                time.sleep(2.0)
                continue
            try:
                dev = race.Race(node)
            except OSError:
                time.sleep(2.0)
                continue

            def on_frame(frame):
                nonlocal burst, last_frame_at, linked
                if frame.opcode == OP_BATTERY and frame.type == T_IND and frame.payload:
                    if time.time() - last_frame_at > BATTERY_BURST_GAP:
                        burst = []
                    burst.append(frame.payload[0])
                    last_frame_at = time.time()
                elif frame.opcode == OP_LINK_STATE and len(frame.payload) >= 3:
                    linked = bool(frame.payload[2])

            dev.on_frame = on_frame
            delay = 1.0
            try:
                with dev:
                    dev.drain(1.5)          # the link-up burst is already queued
                    while not should_stop():
                        if dev.pump():
                            delay = 0.005
                        else:
                            delay = min(delay * 2.0, 1.0)
                        # The run is a gauge animation; the level is where it
                        # settles, so commit only once it has stopped arriving.
                        if burst and time.time() - last_frame_at > BATTERY_BURST_GAP:
                            run, burst = burst, []
                            level = run[-1]
                            if 0 < level <= 100:
                                emit(Reading(percent=level, present=True, at=last_frame_at))
                        time.sleep(delay)
            except race.DeviceGone:
                emit(Reading(present=False))
            except OSError:
                pass
            finally:
                dev.close()
            time.sleep(1.0)


SOURCES = (PlyrHeadsetSource, WLMouseSource)
