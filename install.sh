#!/bin/sh
# DeskWidgets installer for any Linux distribution.
#
#   ./install.sh                 install for the current user into ~/.local (no root needed)
#   sudo ./install.sh --system   install for everyone into /usr/local
#   ./install.sh --prefix DIR    install into DIR (DESTDIR is honoured for packaging)
#   ./install.sh --uninstall     remove a user install (add --system for /usr/local)
#
# Then run `deskwidgets setup` (autostart, Super+Alt+W, GNOME grid helper) and `deskwidgets`.
set -eu

SRC=$(cd "$(dirname "$0")" && pwd)
PREFIX="$HOME/.local"
SYSTEM=0
UNINSTALL=0
while [ $# -gt 0 ]; do
    case $1 in
        --system) PREFIX=/usr/local; SYSTEM=1 ;;
        --prefix) PREFIX=$2; shift ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
DEST="${DESTDIR:-}$PREFIX"
SHARE="$DEST/share/deskwidgets"
UUID=deskwidgets@toksik.github.io

if [ "$UNINSTALL" = 1 ]; then
    [ "$SYSTEM" = 1 ] && rm -f /etc/udev/rules.d/70-deskwidgets-hid.rules
    rm -rf "$SHARE" "$DEST/bin/deskwidgets" \
        "$DEST/share/applications/io.github.toksik.DeskWidgets.Gallery.desktop" \
        "$DEST/share/icons/hicolor/scalable/apps/io.github.toksik.DeskWidgets.svg"
    [ "$SYSTEM" = 1 ] && rm -rf "$DEST/share/gnome-shell/extensions/$UUID" || rm -rf "$HOME/.local/share/gnome-shell/extensions/$UUID"
    echo "DeskWidgets removed from $PREFIX (your widgets and settings in ~/.config/deskwidgets are kept)"
    exit 0
fi

command -v conky >/dev/null || echo "warning: conky is not installed (Debian/Ubuntu: sudo apt install conky-all)" >&2

mkdir -p "$SHARE" "$DEST/bin" "$DEST/share/applications" "$DEST/share/icons/hicolor/scalable/apps"
rm -rf "$SHARE/deskwidgets" "$SHARE/engine" "$SHARE/widgets" "$SHARE/fonts" "$SHARE/gnome-extension" "$SHARE/data" "$SHARE/docs" "$SHARE/examples"
for d in deskwidgets engine widgets fonts gnome-extension data docs examples; do
    cp -r "$SRC/$d" "$SHARE/"
done
find "$SHARE" -name '__pycache__' -type d -prune -exec rm -rf {} +
rm -rf "$SHARE/docs/screenshots"   # README pictures, not needed at runtime
cp "$SRC/bin/deskwidgets" "$DEST/bin/deskwidgets"
chmod 755 "$DEST/bin/deskwidgets"
sed "s|^Exec=deskwidgets|Exec=$PREFIX/bin/deskwidgets|" "$SRC/data/io.github.toksik.DeskWidgets.Gallery.desktop" \
    > "$DEST/share/applications/io.github.toksik.DeskWidgets.Gallery.desktop"
cp "$SRC/data/icons/io.github.toksik.DeskWidgets.svg" "$DEST/share/icons/hicolor/scalable/apps/"
# udev rule: read the battery of wireless gaming mice without root (see data/udev/)
RULES=data/udev/70-deskwidgets-hid.rules
if [ -n "${DESTDIR:-}" ] && [ "$PREFIX" = /usr ]; then
    install -D -m 644 "$SRC/$RULES" "$DESTDIR/usr/lib/udev/rules.d/70-deskwidgets-hid.rules"
elif [ "$SYSTEM" = 1 ]; then
    install -D -m 644 "$SRC/$RULES" /etc/udev/rules.d/70-deskwidgets-hid.rules
    udevadm control --reload 2>/dev/null && udevadm trigger --subsystem-match=hidraw 2>/dev/null || true
fi
if [ "$SYSTEM" = 1 ] || [ -n "${DESTDIR:-}" ]; then
    mkdir -p "$DEST/share/gnome-shell/extensions"
    rm -rf "$DEST/share/gnome-shell/extensions/$UUID"
    cp -r "$SRC/gnome-extension/$UUID" "$DEST/share/gnome-shell/extensions/"
fi
if [ -z "${DESTDIR:-}" ] && command -v update-desktop-database >/dev/null; then
    update-desktop-database "$DEST/share/applications" 2>/dev/null || true
fi

echo "DeskWidgets installed into $PREFIX"
case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) echo "note: add $PREFIX/bin to your PATH" ;; esac
if [ "$SYSTEM" = 0 ] && [ -z "${DESTDIR:-}" ] && [ ! -e /etc/udev/rules.d/70-deskwidgets-hid.rules ] && [ ! -e /usr/lib/udev/rules.d/70-deskwidgets-hid.rules ]; then
    echo "optional, for the battery of Pulsar/Kysona-style wireless mice (needs root once):"
    echo "  sudo install -m 644 $SHARE/$RULES /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw"
fi
echo "next: deskwidgets setup   # autostart, Super+Alt+W, GNOME grid helper"
echo "      deskwidgets         # start the widgets (or open \"DeskWidgets\" from the app menu)"
