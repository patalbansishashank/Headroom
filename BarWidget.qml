import QtQuick
import QtQuick.Layouts
import Quickshell
import qs.Commons
import qs.Widgets
import qs.Services.UI

// A headphone glyph with the headset's battery level beside it.
//
// Deliberately not the same shape as Noctalia's own Battery widget: that one is
// the laptop, this one is the headset, and at a glance in the bar they must not
// be mistaken for each other. Hence the headphone icon rather than a second
// battery pill.
Item {
  id: root

  property var pluginApi: null
  property ShellScreen screen
  property string widgetId: ""
  property string section: ""
  property int sectionWidgetIndex: -1
  property int sectionWidgetsCount: 0

  readonly property string screenName: screen?.name ?? ""
  readonly property string barPosition: Settings.getBarPositionForScreen(screenName)
  readonly property bool isBarVertical: barPosition === "left" || barPosition === "right"
  readonly property real capsuleHeight: Style.getCapsuleHeightForScreen(screenName)

  readonly property var main: pluginApi?.mainInstance ?? null
  readonly property var config: pluginApi?.pluginSettings ?? null

  readonly property int percent: main?.percent ?? -1
  readonly property bool hasReading: main?.hasReading ?? false
  readonly property bool donglePresent: main?.donglePresent ?? false
  readonly property var linked: main?.linked ?? null
  readonly property real ageSeconds: main?.ageSeconds ?? -1

  readonly property int warnBelow: config?.warnBelow ?? 15
  readonly property bool hideWhenUnavailable: config?.hideWhenUnavailable ?? false

  // A level nobody has refreshed in hours is history, not status. Show it
  // faded rather than pretending it is current.
  readonly property bool stale: ageSeconds > 3600
  readonly property bool low: hasReading && percent < warnBelow

  readonly property bool available: donglePresent && hasReading
  visible: available || !hideWhenUnavailable

  readonly property color tint: {
    if (!available)
      return Color.mOnSurfaceVariant
    if (low)
      return Color.mError
    return Color.mOnSurface
  }

  readonly property real iconSize: capsuleHeight * 0.55

  implicitWidth: isBarVertical ? capsuleHeight : row.implicitWidth + Style.marginM * 2
  implicitHeight: isBarVertical ? column.implicitHeight + Style.marginM * 2 : capsuleHeight

  Rectangle {
    anchors.fill: parent
    radius: Math.round(height / 2)
    color: Style.capsuleColor
    border.color: root.low && root.available ? Color.mError : Style.capsuleBorderColor
    border.width: Math.max(1, Style.borderS)
  }

  // Horizontal bar: icon then the number.
  RowLayout {
    id: row
    anchors.centerIn: parent
    visible: !root.isBarVertical
    spacing: Style.marginXS

    NIcon {
      icon: root.donglePresent ? "headphones" : "headphones-off"
      pointSize: root.iconSize
      color: root.tint
      opacity: root.stale ? 0.55 : 1.0
    }

    NText {
      visible: root.hasReading
      text: root.percent + "%"
      pointSize: Style.fontSizeS
      font.weight: Font.DemiBold
      color: root.tint
      opacity: root.stale ? 0.55 : 1.0
    }
  }

  // Vertical bar: icon over the number, no percent sign (there is no room).
  ColumnLayout {
    id: column
    anchors.centerIn: parent
    visible: root.isBarVertical
    spacing: 0

    NIcon {
      Layout.alignment: Qt.AlignHCenter
      icon: root.donglePresent ? "headphones" : "headphones-off"
      pointSize: root.iconSize
      color: root.tint
      opacity: root.stale ? 0.55 : 1.0
    }

    NText {
      Layout.alignment: Qt.AlignHCenter
      visible: root.hasReading
      text: String(root.percent)
      pointSize: Style.fontSizeXS
      font.weight: Font.DemiBold
      color: root.tint
      opacity: root.stale ? 0.55 : 1.0
    }
  }

  function tooltipText() {
    if (!donglePresent)
      return "PLYR 720: dongle not connected"
    if (!hasReading) {
      if (linked === false)
        return "PLYR 720: headset off"
      return "PLYR 720: waiting for the headset to report"
    }
    var when = new Date(main.updatedAt)
    var clock = Qt.formatTime(when, "HH:mm")
    var body = `Crusher PLYR 720: ${percent}%`
    if (ageSeconds > 120)
      body += `, as of ${clock}`
    if (linked === false)
      body += " (headset off)"
    return body
  }

  MouseArea {
    anchors.fill: parent
    hoverEnabled: true
    onEntered: TooltipService.show(root, root.tooltipText(),
                                  BarService.getTooltipDirection(root.screen?.name))
    onExited: TooltipService.hide()
  }
}
