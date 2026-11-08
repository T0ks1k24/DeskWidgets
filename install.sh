#!/bin/sh
# DeskWidgets installer for any Linux distribution.
#
#   ./install.sh                 build and install for the current user into ~/.local (no root needed)
#   sudo ./install.sh --system   install for everyone into /usr/local
#   ./install.sh --prefix DIR    install into DIR
#   ./install.sh --uninstall     remove a user install (add --system for /usr/local)
#
# DeskWidgets is one executable (widgets, fonts and icons are inside it). It is built with cargo from the
# Rust toolchain (https://rustup.rs); a ready `deskwidgets` binary next to this script is used if there is one.
# Then run `deskwidgets setup` (autostart, hotkey, menu entry) and `deskwidgets`.
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
APP_ID=io.github.toksik.DeskWidgets

if [ "$UNINSTALL" = 1 ]; then
    [ "$SYSTEM" = 1 ] && rm -f /etc/udev/rules.d/70-deskwidgets-hid.rules
    rm -f "$PREFIX/bin/deskwidgets" \
        "$PREFIX/share/applications/$APP_ID.Gallery.desktop" \
        "$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg"
    echo "DeskWidgets removed from $PREFIX (your widgets and settings in ~/.config/deskwidgets are kept)"
    exit 0
fi

if [ -x "$SRC/deskwidgets" ] && [ ! -d "$SRC/src" ]; then
    BINARY="$SRC/deskwidgets"
else
    command -v cargo >/dev/null || { echo "cargo is required: install the Rust toolchain from https://rustup.rs" >&2; exit 1; }
    (cd "$SRC" && cargo build --release --locked)
    BINARY="$SRC/target/release/deskwidgets"
fi

mkdir -p "$PREFIX/bin" "$PREFIX/share/applications" "$PREFIX/share/icons/hicolor/scalable/apps"
# replace, never write through: an older install left a symlink here that points into /usr/bin
rm -f "$PREFIX/bin/deskwidgets" "$PREFIX/share/applications/$APP_ID.Gallery.desktop" \
    "$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg"
install -m 755 "$BINARY" "$PREFIX/bin/deskwidgets"
cp "$SRC/assets/icons/$APP_ID.svg" "$PREFIX/share/icons/hicolor/scalable/apps/"
sed "s|^Exec=.*|Exec=$PREFIX/bin/deskwidgets gallery|" "$SRC/packaging/linux/$APP_ID.Gallery.desktop" \
    > "$PREFIX/share/applications/$APP_ID.Gallery.desktop"

if [ "$SYSTEM" = 1 ] && [ -d /etc/udev/rules.d ]; then
    install -m 644 "$SRC/assets/udev/70-deskwidgets-hid.rules" /etc/udev/rules.d/
    command -v udevadm >/dev/null && { udevadm control --reload; udevadm trigger --subsystem-match=hidraw; } || true
fi

echo "DeskWidgets installed in $PREFIX. Next: deskwidgets setup && deskwidgets   (deskwidgets doctor checks what is missing)"
case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) echo "note: add $PREFIX/bin to your PATH" ;; esac
