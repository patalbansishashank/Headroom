import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons

// Headroom: battery levels for wireless peripherals.
//
// All device work happens in bin/headroomd.py, which owns the connections and
// publishes one JSON line whenever anything changes. This file supervises that
// daemon and republishes its state, so the shell never touches a hidraw node.
//
// Adding a device is a change to bin/headroom_sources.py alone. Nothing here
// names a headset or a mouse: the list is whatever the daemon reports.
Item {
  id: root

  property var pluginApi: null

  // Array of { id, name, icon, note, percent, charging, present, updated }.
  // `percent` is null when nothing has been reported yet.
  property var devices: []

  // Bumped whenever time passes, so age-dependent bindings re-evaluate
  // without the daemon having to say anything.
  property int ageTick: 0

  readonly property var known: devices.filter(d => d.percent !== null && d.percent !== undefined)
  readonly property bool hasAny: known.length > 0
  readonly property int lowest: {
    var worst = 101
    for (var i = 0; i < known.length; i++)
      worst = Math.min(worst, known[i].percent)
    return worst === 101 ? -1 : worst
  }

  readonly property string daemonPath: (pluginApi?.pluginDir ?? "") + "/bin/headroomd.py"
  readonly property bool wantRunning: pluginApi !== null && pluginApi.manifest !== null

  readonly property int exitAlreadyRunning: 3
  property int restartBackoff: 3000

  function ageOf(device) {
    if (!device || !device.updated)
      return -1
    return (Date.now() - device.updated * 1000) / 1000
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
          var parsed = JSON.parse(line)
          if (parsed.devices !== undefined) {
            root.devices = parsed.devices
            root.restartBackoff = 3000      // it started; forget past contention
          }
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
      if (!root.wantRunning) {
        root.devices = []
        return
      }
      // Losing the device lock is not a crash: another instance is serving,
      // and it only emits on change, so clearing the display here would leave
      // it blank until the next real change. Keep what we have.
      if (code === root.exitAlreadyRunning) {
        root.restartBackoff = Math.min(root.restartBackoff * 2, 60000)
        Logger.w("Headroom", `devices held by another instance; retrying in ${root.restartBackoff / 1000}s`)
      } else {
        root.devices = []
        Logger.w("Headroom", `daemon exited (${code}); restarting`)
      }
      restartTimer.interval = root.restartBackoff
      restartTimer.restart()
    }
  }

  Timer {
    id: restartTimer
    interval: root.restartBackoff
    repeat: false
    onTriggered: if (root.wantRunning && !daemon.running) daemon.running = true
  }

  // One tick a minute is enough to fade a reading as it ages; anything faster
  // would be redrawing for no visible change.
  Timer {
    interval: 60000
    repeat: true
    running: root.wantRunning && root.hasAny
    onTriggered: root.ageTick++
  }
}
