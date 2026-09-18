import QtQuick
import QtQuick.Layouts
import Quickshell
import qs.Commons
import qs.Services.UI
import qs.Widgets

// One ring gauge per device: the arc is the charge, the glyph says which
// device, the colour says whether you need to care.
//
// The capsule is drawn here rather than with BarPill, which only carries an
// icon and a text. The geometry constants below are BarPill's own, so the
// widget still lines up exactly with its neighbours; guessing them by eye
// produces a pill that sits visibly taller, which happened once already.
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
  readonly property bool isVertical: barPosition === "left" || barPosition === "right"
  readonly property real capsuleHeight: Style.getCapsuleHeightForScreen(screenName)

  readonly property var main: pluginApi?.mainInstance ?? null
  readonly property var config: pluginApi?.pluginSettings ?? null

  readonly property var devices: main?.devices ?? []
  readonly property bool hideWhenUnavailable: config?.hideWhenUnavailable ?? false
  readonly property bool hideAbsent: config?.hideAbsentDevices ?? true

  // Devices worth a gauge: present, or holding a last-known level.
  readonly property var shown: devices.filter(function (d) {
    if (!root.hideAbsent)
      return true
    return d.present || (d.percent !== null && d.percent !== undefined)
  })

  visible: shown.length > 0 || !hideWhenUnavailable

  // ---- colour ------------------------------------------------------------
  //
  // White while there is nothing to think about, easing into amber as the
  // charge gets low and into red when it is urgent. The bands are the
  // transitions, not steps: 30 to 20 fades white to amber, 10 to 5 fades
  // amber to red, and outside those the colour is flat.
  //
  // "White" is the theme's foreground rather than literal #fff, so the gauge
  // stays legible if the bar is ever light-on-dark.
  readonly property color colorFull: Color.mOnSurface
  readonly property color colorWarn: "#e3b341"
  readonly property color colorCritical: "#f85149"

  function mixColor(from, to, t) {
    var k = Math.max(0, Math.min(1, t))
    return Qt.rgba(from.r + (to.r - from.r) * k,
                   from.g + (to.g - from.g) * k,
                   from.b + (to.b - from.b) * k, 1)
  }

  function levelColor(percent) {
    if (percent === null || percent === undefined)
      return Color.mOnSurfaceVariant
    if (percent >= 30)
      return colorFull
    if (percent >= 20)
      return mixColor(colorFull, colorWarn, (30 - percent) / 10)
    if (percent >= 10)
      return colorWarn
    if (percent >= 5)
      return mixColor(colorWarn, colorCritical, (10 - percent) / 5)
    return colorCritical
  }

  // Below this the colour is doing the talking, so the whole glyph takes it
  // rather than just a sliver of fill. A red 5% that is five pixels tall is
  // not a warning anyone will see.
  function isUrgent(percent) {
    return percent !== null && percent !== undefined && percent < 30
  }

  // ---- geometry (BarPill's own numbers) ----------------------------------
  // A glyph, and beside it a slim upright bar that is the level. The glyph
  // carries the colour so a low battery is visible from across the room; the
  // bar carries the precision.
  readonly property real glyphSize: Style.toOdd(capsuleHeight * 0.50)
  readonly property real gaugeHeight: Math.round(capsuleHeight * 0.62)
  readonly property real barWidth: Math.max(3, Math.round(capsuleHeight * 0.13))
  readonly property real barGap: Math.max(2, Math.round(capsuleHeight * 0.10))
  readonly property real gaugeSpacing: Math.round(capsuleHeight * 0.26)

  readonly property real contentWidth: isVertical
    ? capsuleHeight
    : Math.round(layout.implicitWidth + Style.margin2M)
  readonly property real contentHeight: isVertical
    ? Math.round(layout.implicitHeight + Style.margin2M)
    : capsuleHeight

  implicitWidth: contentWidth
  implicitHeight: contentHeight

  Rectangle {
    anchors.centerIn: parent
    width: root.contentWidth
    height: root.contentHeight
    radius: Style.radiusM
    color: Style.capsuleColor
    border.color: Style.capsuleBorderColor
    border.width: Style.capsuleBorderWidth
  }

  GridLayout {
    id: layout
    anchors.centerIn: parent
    columns: root.isVertical ? 1 : root.shown.length
    rows: root.isVertical ? root.shown.length : 1
    columnSpacing: root.gaugeSpacing
    rowSpacing: root.gaugeSpacing

    Repeater {
      model: root.shown

      delegate: RowLayout {
        id: gauge
        required property var modelData

        readonly property var percent: modelData.percent
        readonly property bool hasLevel: percent !== null && percent !== undefined
        readonly property real fraction: hasLevel ? Math.max(0, Math.min(100, percent)) / 100 : 0
        readonly property color tint: root.levelColor(hasLevel ? percent : null)
        readonly property string glyph: modelData.icon || "battery"

        // Old readings fade rather than vanish: still true, just not fresh.
        readonly property real freshness: {
          root.main ? root.main.ageTick : 0          // re-evaluate as time passes
          var age = root.main ? root.main.ageOf(modelData) : -1
          return (age >= 0 && age > 3600) ? 0.55 : 1.0
        }

        spacing: root.barGap
        opacity: freshness

        Behavior on opacity {
          NumberAnimation { duration: Style.animationNormal }
        }

        // The device. Fully tinted, so the warning colour occupies real area
        // instead of a few pixels at the bottom of a fill.
        NIcon {
          Layout.alignment: Qt.AlignVCenter
          icon: gauge.glyph
          pointSize: root.glyphSize
          applyUiScale: false
          color: gauge.hasLevel
            ? gauge.tint
            : Qt.rgba(Color.mOnSurfaceVariant.r, Color.mOnSurfaceVariant.g,
                      Color.mOnSurfaceVariant.b, 0.55)

          Behavior on color {
            ColorAnimation { duration: Style.animationNormal }
          }
        }

        // The level. A small upright cell that empties downwards.
        Item {
          Layout.alignment: Qt.AlignVCenter
          implicitWidth: root.barWidth
          implicitHeight: root.gaugeHeight
          visible: gauge.hasLevel

          Rectangle {                       // track
            anchors.fill: parent
            radius: width / 2
            color: Qt.rgba(Color.mOnSurfaceVariant.r, Color.mOnSurfaceVariant.g,
                           Color.mOnSurfaceVariant.b, 0.28)
          }

          Rectangle {                       // charge
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            // Never let a non-zero charge round away to nothing.
            height: Math.max(width, parent.height * gauge.fraction)
            radius: width / 2
            color: gauge.tint

            Behavior on height {
              NumberAnimation { duration: Style.animationNormal; easing.type: Easing.OutCubic }
            }
            Behavior on color {
              ColorAnimation { duration: Style.animationNormal }
            }
          }

          // Charging pip, static so it costs nothing to show.
          Rectangle {
            visible: modelData.charging
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            width: parent.width
            height: width
            radius: width / 2
            color: root.colorFull
          }
        }
      }
    }
  }

  function tooltipText() {
    if (root.devices.length === 0)
      return "Headroom\nNo devices"
    var lines = []
    for (var i = 0; i < root.devices.length; i++) {
      var d = root.devices[i]
      var age = root.main ? root.main.ageOf(d) : -1
      var line = d.name + ": "
      if (d.percent === null || d.percent === undefined) {
        line += d.present ? "not reported yet" : "not connected"
      } else {
        line += d.percent + "%"
        if (d.charging)
          line += ", charging"
        if (age > 120) {
          var when = Qt.formatTime(new Date(d.updated * 1000), "HH:mm")
          line += "  (at " + when + ")"
        }
        if (!d.present)
          line += "  (disconnected)"
      }
      if (d.note && age > 120)
        line += "\n    " + d.note
      lines.push(line)
    }
    return lines.join("\n")
  }

  MouseArea {
    anchors.fill: parent
    hoverEnabled: true
    onEntered: TooltipService.show(root, root.tooltipText(),
                                  BarService.getTooltipDirection(root.screen?.name))
    onExited: TooltipService.hide()
  }
}
