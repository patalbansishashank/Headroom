#!/usr/bin/env bash
# Install Headroom as a Noctalia plugin.
#
#   Source of truth : this folder (edit + version-control here)
#   Plugin runtime  : ~/.config/noctalia/plugins/headroom/   (Noctalia loads from here)
#   Device access   : /etc/udev/rules.d/70-headroom-devices.rules
#
# We COPY into the plugin dir by default rather than symlink: this source folder
# may live on a removable mount, and Noctalia starts the daemon from the plugin
# dir — a login must never depend on that drive being present.
#
# Usage:
#   bash install.sh           # copy-install (recommended)
#   bash install.sh --link    # symlink instead (dev iteration; accepts mount risk)
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/noctalia/plugins/headroom"
RULE_SRC="$SRC/udev/70-headroom-devices.rules"
RULE_DST="/etc/udev/rules.d/70-headroom-devices.rules"
LINK=0
[[ "${1:-}" == "--link" ]] && LINK=1

echo "── Headroom install ──────────────────────────────"

# 1) never ship a daemon that cannot start
for f in bin/*.py; do
  if ! python3 -c "import ast; ast.parse(open('$SRC/$f').read())"; then
    echo "✗ syntax error in $f — aborting, nothing installed"; exit 1
  fi
done
echo "✓ python syntax OK"

# 2) device access. The dongle's vendor HID node is root-only by default.
#
#    The 70- prefix matters and is not cosmetic: 73-seat-late.rules is what runs
#    the uaccess builtin, and it only acts on devices already tagged by the time
#    it is evaluated. A 99- prefix sets the tag too late and grants nothing,
#    silently — the device ends up tagged but with no ACL.
if cmp -s "$RULE_SRC" "$RULE_DST" 2>/dev/null; then
  echo "✓ udev rule already current"
elif sudo -n true 2>/dev/null || [[ -t 0 ]]; then
  echo "  installing udev rule (needs root):"
  if sudo install -m 644 "$RULE_SRC" "$RULE_DST" \
     && sudo rm -f /etc/udev/rules.d/99-skullcandy-plyr.rules \
                   /etc/udev/rules.d/70-skullcandy-plyr.rules \
     && sudo udevadm control --reload \
     && sudo udevadm trigger --action=add --subsystem-match=hidraw; then
    echo "✓ udev rule installed"
  else
    echo "⚠ could not install the udev rule; devices may be unreadable"
  fi
else
  # Non-interactive and no cached sudo: say so rather than aborting, since
  # the plugin itself installs fine and access may already be granted.
  echo "⚠ skipping the udev rule (no terminal for sudo)."
  echo "  Re-run this script from a terminal if a device reads as unavailable."
fi

# 3) verify we can actually reach the device before claiming success
if node=$(python3 -c "
import sys; sys.path.insert(0, '$SRC/bin')
from headroom_race import find_node
print(find_node() or '')
"); then
  if [[ -z "$node" ]]; then
    echo "… dongle not plugged in; skipping the access check"
  elif [[ -r "$node" && -w "$node" ]]; then
    echo "✓ $node is readable and writable"
  else
    echo "⚠ $node exists but is not accessible yet."
    echo "  Unplug and replug the dongle, then re-run this script."
  fi
fi

# 4) plugin dir
mkdir -p "$(dirname "$PLUGIN_DIR")"
if [[ "$LINK" == 1 ]]; then
  rm -rf "$PLUGIN_DIR"
  ln -sfn "$SRC" "$PLUGIN_DIR"
  echo "✓ linked  $PLUGIN_DIR -> $SRC   (dev mode)"
else
  mkdir -p "$PLUGIN_DIR"
  for item in manifest.json Main.qml BarWidget.qml Settings.qml README.md; do
    [[ -e "$SRC/$item" ]] && cp -r "$SRC/$item" "$PLUGIN_DIR/"
  done
  # bin/ without the build cruft, so __pycache__ from a dev run is not shipped
  mkdir -p "$PLUGIN_DIR/bin"
  cp "$SRC"/bin/*.py "$PLUGIN_DIR/bin/"
  rm -rf "$PLUGIN_DIR/bin/__pycache__"
  echo "✓ copied plugin -> $PLUGIN_DIR"
fi

cat <<MSG

Done. To finish:

  In Noctalia → Settings → Plugins → Installed, enable "Headroom",
  then add the Headroom widget to a bar section.

The daemon starts with the plugin. To check it by hand:

  $PLUGIN_DIR/bin/headroomctl.py --identify   # confirm the dongle answers
  $PLUGIN_DIR/bin/headroomctl.py              # last known battery level
  $PLUGIN_DIR/bin/headroomctl.py --frames     # watch raw protocol frames
MSG
