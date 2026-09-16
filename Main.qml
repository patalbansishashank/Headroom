import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons

// Headroom: headset battery for the Skullcandy Crusher PLYR 720.
//
// All the protocol work happens in bin/headroomd.py, which holds a RACE session
// open on the dongle's vendor HID interface. This file only supervises that
// daemon and republishes what it reports, so the shell never touches hidraw.
//
// The daemon prints one JSON line per state change and is otherwise silent, so
// an idle headset costs nothing.
Item {
  id: root

  property var pluginApi: null

  // ---- state, consumed by BarWidget.qml ----
  property int percent: -1            // -1 when no level is known
  property bool donglePresent: false
  property var linked: null           // true / false / null when unreported
  property double updatedAt: 0        // epoch ms of the last level

  readonly property bool hasReading: percent >= 0
  readonly property real ageSeconds: updatedAt > 0 ? (Date.now() - updatedAt) / 1000 : -1

  readonly property string daemonPath: (pluginApi?.pluginDir ?? "") + "/bin/headroomd.py"
  readonly property bool wantRunning: pluginApi !== null && pluginApi.manifest !== null

  // Exit code the daemon uses for "another instance already holds the device".
  readonly property int exitAlreadyRunning: 3
  property int restartBackoff: 3000

  function reset() {
    percent = -1
    donglePresent = false
    linked = null
  }

  Process {
    id: daemon

    command: ["python3", root.daemonPath, "--emit"]
    running: root.wantRunning

    stdout: SplitParser {
      onRead: (data) => {
        var line = data.trim()
        if (line === "")
          return
        try {
          var s = JSON.parse(line)
          root.percent = (s.percent === null || s.percent === undefined) ? -1 : s.percent
          root.donglePresent = s.dongle === true
          root.linked = (s.linked === undefined) ? null : s.linked
          root.updatedAt = s.updated ? s.updated * 1000 : 0
          root.restartBackoff = 3000        // it started; forget past contention
        } catch (e) {
          Logger.w("Headroom", "unparsable line from daemon:", line)
        }
      }
    }

    stderr: SplitParser {
      onRead: (data) => {
        var line = data.trim()
        if (line !== "")
          Logger.w("Headroom", line)
      }
    }

    onExited: (code) => {
      root.reset()
      if (!root.wantRunning)
        return

      // Losing the race for the device lock is not a crash, and retrying every
      // few seconds forever just fills the log. Back off instead, up to a
      // minute, and reset once a start actually sticks.
      if (code === root.exitAlreadyRunning) {
        root.restartBackoff = Math.min(root.restartBackoff * 2, 60000)
        Logger.w("Headroom", `device held by another instance; retrying in ${root.restartBackoff / 1000}s`)
      } else {
        Logger.w("Headroom", `daemon exited (${code}); restarting`)
      }
      restartTimer.interval = root.restartBackoff
      restartTimer.restart()
    }
  }

  // The daemon is meant to run for the whole session. If it dies anyway, come
  // back after a pause rather than hammering a broken install.
  Timer {
    id: restartTimer
    interval: root.restartBackoff
    repeat: false
    onTriggered: if (root.wantRunning && !daemon.running) daemon.running = true
  }

  // Keep `ageSeconds` moving so the widget can dim a level that has gone stale
  // without the daemon having to say anything.
  Timer {
    interval: 30000
    repeat: true
    running: root.wantRunning && root.hasReading
    onTriggered: root.updatedAtChanged()
  }
}
