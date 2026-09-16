import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Services.UI
import qs.Widgets

// Headroom settings. There is deliberately very little here: the battery level
// is pushed by the dongle rather than polled, so there is no interval to tune.
ColumnLayout {
  id: root

  property var pluginApi: null

  property string editDisplayMode: "alwaysShow"
  property int  editWarnBelow: 15
  property bool editHideWhenUnavailable: false
  property bool _loaded: false

  spacing: Style.marginL

  readonly property var main: pluginApi?.mainInstance ?? null

  function _load() {
    if (!pluginApi?.pluginSettings)
      return
    _loaded = false
    var s = pluginApi.pluginSettings
    editDisplayMode = s.displayMode || "alwaysShow"
    editWarnBelow = s.warnBelow ?? 15
    editHideWhenUnavailable = s.hideWhenUnavailable ?? false
    _loaded = true
  }

  function saveSettings() {
    if (!pluginApi || !_loaded)
      return
    var s = pluginApi.pluginSettings
    s.displayMode = editDisplayMode
    s.warnBelow = Math.max(0, Math.min(100, editWarnBelow))
    s.hideWhenUnavailable = editHideWhenUnavailable
    pluginApi.saveSettings()
  }

  NHeader {
    Layout.fillWidth: true
    label: "Headset battery"
    description: "Reads the Crusher PLYR 720 through its 2.4 GHz dongle, so the headset's Bluetooth stays free for your phone."
  }

  NComboBox {
    Layout.fillWidth: true
    label: "Display mode"
    description: "Whether the percentage sits beside the icon, or only appears on hover."
    minimumWidth: 200
    model: [
      { "key": "alwaysShow", "name": "Always show" },
      { "key": "onhover", "name": "On hover" },
      { "key": "alwaysHide", "name": "Icon only" }
    ]
    currentKey: root.editDisplayMode
    defaultValue: "alwaysShow"
    onSelected: (key) => {
      root.editDisplayMode = key
      root.saveSettings()
    }
  }

  NSpinBox {
    Layout.fillWidth: true
    label: "Warn below"
    description: "The level turns red at or under this percentage."
    from: 0
    to: 100
    stepSize: 5
    suffix: "%"
    value: root.editWarnBelow
    onValueChanged: {
      if (root._loaded && value !== root.editWarnBelow) {
        root.editWarnBelow = value
        root.saveSettings()
      }
    }
  }

  NToggle {
    Layout.fillWidth: true
    label: "Hide when unavailable"
    description: "Off: the widget stays in the bar with a struck-through headphone icon when the dongle is unplugged. On: it disappears entirely."
    checked: root.editHideWhenUnavailable
    onToggled: (v) => {
      root.editHideWhenUnavailable = v
      root.saveSettings()
    }
  }

  NDivider { Layout.fillWidth: true }

  NLabel {
    Layout.fillWidth: true
    label: "Status"
    description: {
      if (!root.main)
        return "Plugin is not running."
      if (!root.main.donglePresent)
        return "Dongle not detected. Check that it is plugged in, and that udev/70-skullcandy-plyr.rules is installed."
      var lines = ["Dongle connected."]
      if (root.main.hasReading) {
        lines.push(`Last reported ${root.main.percent}%.`)
      } else {
        lines.push("No battery level reported yet. The dongle pushes the level rather than answering a request, so this fills in when the headset next reports.")
      }
      if (root.main.linked === false)
        lines.push("Headset is currently off.")
      return lines.join(" ")
    }
  }

  Component.onCompleted: _load()
  onPluginApiChanged: _load()
}
