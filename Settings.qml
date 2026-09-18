import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Services.UI
import qs.Widgets

// Headroom settings. Deliberately small: levels are pushed by the hardware
// rather than polled on a schedule, so there is no interval to tune.
ColumnLayout {
  id: root

  property var pluginApi: null

  property bool editHideWhenUnavailable: false
  property bool editHideAbsent: true
  property bool _loaded: false

  spacing: Style.marginL

  readonly property var main: pluginApi?.mainInstance ?? null

  function _load() {
    if (!pluginApi?.pluginSettings)
      return
    _loaded = false
    var s = pluginApi.pluginSettings
    editHideWhenUnavailable = s.hideWhenUnavailable ?? false
    editHideAbsent = s.hideAbsentDevices ?? true
    _loaded = true
  }

  function saveSettings() {
    if (!pluginApi || !_loaded)
      return
    var s = pluginApi.pluginSettings
    s.hideWhenUnavailable = editHideWhenUnavailable
    s.hideAbsentDevices = editHideAbsent
    pluginApi.saveSettings()
  }

  NHeader {
    Layout.fillWidth: true
    label: "Peripheral batteries"
    description: "One ring per device. The ring fills with the charge and shifts from white through amber to red as it runs down."
  }

  NToggle {
    Layout.fillWidth: true
    label: "Hide disconnected devices"
    description: "On: a device drops out of the bar once it is gone and has no last-known level. Off: it stays, greyed."
    checked: root.editHideAbsent
    onToggled: (v) => {
      root.editHideAbsent = v
      root.saveSettings()
    }
  }

  NToggle {
    Layout.fillWidth: true
    label: "Hide the widget when empty"
    description: "On: nothing to show means nothing in the bar. Off: the capsule stays put so the bar does not shift around."
    checked: root.editHideWhenUnavailable
    onToggled: (v) => {
      root.editHideWhenUnavailable = v
      root.saveSettings()
    }
  }

  NDivider { Layout.fillWidth: true }

  NLabel {
    Layout.fillWidth: true
    label: "Devices"
    description: {
      if (!root.main)
        return "Plugin is not running."
      var list = root.main.devices || []
      if (list.length === 0)
        return "No known devices detected."
      var lines = []
      for (var i = 0; i < list.length; i++) {
        var d = list[i]
        var state = (d.percent === null || d.percent === undefined)
          ? (d.present ? "connected, no level reported yet" : "not connected")
          : d.percent + "%" + (d.charging ? ", charging" : "")
        lines.push(d.name + " — " + state + (d.note ? ". " + d.note + "." : ""))
      }
      return lines.join("\n")
    }
  }

  Component.onCompleted: _load()
  onPluginApiChanged: _load()
}
