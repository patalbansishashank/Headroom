import QtQuick
import Quickshell
import qs.Commons
import qs.Modules.Bar.Extras
import qs.Services.UI
import qs.Widgets

// The headset's battery level in the bar.
//
// This uses BarPill rather than drawing its own capsule. Hand-rolling one looks
// close but is not: the shared pill sizes its icon at 0.48 of the capsule
// height, uses radiusM rather than a full round, and takes its border width
// from the theme. Reimplementing that by eye produces a pill that sits visibly
// taller than its neighbours, which is exactly what happened here first.
//
// The headphone icon is the point of difference from Noctalia's own Battery
// widget: that one is the laptop, this one is the headset, and in the bar they
// must not read as the same thing.
Item {
  id: root

  property var pluginApi: null
  property ShellScreen screen
  property string widgetId: ""
  property string section: ""
  property int sectionWidgetIndex: -1
  property int sectionWidgetsCount: 0

  readonly property var main: pluginApi?.mainInstance ?? null
  readonly property var config: pluginApi?.pluginSettings ?? null

  readonly property int percent: main?.percent ?? -1
  readonly property bool hasReading: main?.hasReading ?? false
  readonly property bool donglePresent: main?.donglePresent ?? false
  readonly property var linked: main?.linked ?? null
  readonly property real ageSeconds: main?.ageSeconds ?? -1

  readonly property int warnBelow: config?.warnBelow ?? 15
  readonly property bool hideWhenUnavailable: config?.hideWhenUnavailable ?? false
  readonly property string displayMode: config?.displayMode ?? "alwaysShow"

  // A level nobody has refreshed in an hour is history, not status. The dongle
  // only volunteers it when the headset links, so this happens routinely.
  readonly property bool stale: ageSeconds > 3600
  readonly property bool low: hasReading && percent < warnBelow

  readonly property bool available: donglePresent && hasReading
  visible: available || !hideWhenUnavailable

  implicitWidth: pill.width
  implicitHeight: pill.height

  BarPill {
    id: pill

    screen: root.screen
    oppositeDirection: BarService.getPillDirection(root)

    icon: root.donglePresent ? "headphones" : "headphones-off"
    text: root.hasReading ? String(root.percent) : ""
    suffix: root.hasReading ? "%" : ""

    autoHide: false
    forceOpen: root.displayMode === "alwaysShow" && root.hasReading
    forceClose: root.displayMode === "alwaysHide" || !root.hasReading

    // Only colour the pill when it is telling you something you must act on.
    customTextIconColor: root.low ? Color.mError : "transparent"

    // Faded while the reading is old, so a stale number never reads as live.
    opacity: root.stale ? 0.6 : 1.0
    Behavior on opacity {
      NumberAnimation {
        duration: Style.animationNormal
      }
    }

    tooltipText: {
      if (!root.donglePresent)
        return "PLYR 720\nDongle not connected"
      if (!root.hasReading) {
        var head = root.linked === false ? "PLYR 720\nHeadset off"
                                         : "PLYR 720\nHeadset connected"
        return head + "\nBattery not reported yet; it arrives when the headset links"
      }

      var lines = [`Crusher PLYR 720: ${root.percent}%`]
      if (root.ageSeconds > 120 && root.main?.updatedAt) {
        lines.push("Measured at " + Qt.formatTime(new Date(root.main.updatedAt), "HH:mm"))
        // Worth saying plainly: this is not a number that ticks down live.
        lines.push("Refreshes when the headset links")
      }
      if (root.linked === false)
        lines.push("Headset is currently off")
      return lines.join("\n")
    }
  }
}
