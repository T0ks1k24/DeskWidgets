#!/bin/sh
# DeskWidgets installer for any Linux distribution.
#
#   ./install.sh                 install for the current user into ~/.local (no root needed)
#   sudo ./install.sh --system   install for everyone into /usr/local
#   ./install.sh --prefix DIR    install into DIR
#   ./install.sh --uninstall     remove a user install (add --system for /usr/local)
#
# DeskWidgets lives in its own virtual environment that can also see the system's Python packages
# (so PyGObject, GTK and pycairo from your distribution are used when present). Needs python3 with
# the venv module; see the README for the packages to install first.
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
DEST="$PREFIX"
HOME_DIR="$DEST/share/deskwidgets"
VENV="$HOME_DIR/venv"
APP_ID=io.github.toksik.DeskWidgets

if [ "$UNINSTALL" = 1 ]; then
    [ "$SYSTEM" = 1 ] && rm -f /etc/udev/rules.d/70-deskwidgets-hid.rules
    rm -rf "$HOME_DIR" "$DEST/bin/deskwidgets" \
        "$DEST/share/applications/$APP_ID.Gallery.desktop" \
        "$DEST/share/icons/hicolor/scalable/apps/$APP_ID.svg"
    echo "DeskWidgets removed from $PREFIX (your widgets and settings in ~/.config/deskwidgets are kept)"
    exit 0
fi

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' || { echo "Python 3.12 or newer is required" >&2; exit 1; }

mkdir -p "$HOME_DIR" "$DEST/bin" "$DEST/share/applications" "$DEST/share/icons/hicolor/scalable/apps"
python3 -m venv --system-site-packages "$VENV" || { echo "python3-venv is missing (Debian/Ubuntu: sudo apt install python3-venv)" >&2; exit 1; }
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet "$SRC"

# replace, never write through: an older install left a symlink here that points into /usr/bin
rm -f "$DEST/bin/deskwidgets" "$DEST/share/applications/$APP_ID.Gallery.desktop" \
    "$DEST/share/icons/hicolor/scalable/apps/$APP_ID.svg"
cat > "$DEST/bin/deskwidgets" <<LAUNCHER
#!/bin/sh
exec "$PREFIX/share/deskwidgets/venv/bin/python" -m deskwidgets "\$@"
LAUNCHER
chmod 755 "$DEST/bin/deskwidgets"

cp "$SRC/deskwidgets/assets/icons/$APP_ID.svg" "$DEST/share/icons/hicolor/scalable/apps/"
sed "s|^Exec=.*|Exec=$PREFIX/bin/deskwidgets gallery|" "$SRC/packaging/linux/$APP_ID.Gallery.desktop" \
    > "$DEST/share/applications/$APP_ID.Gallery.desktop"

if [ "$SYSTEM" = 1 ] && [ -d /etc/udev/rules.d ]; then
    install -m 644 "$SRC/deskwidgets/assets/udev/70-deskwidgets-hid.rules" /etc/udev/rules.d/
    command -v udevadm >/dev/null && { udevadm control --reload; udevadm trigger --subsystem-match=hidraw; } || true
fi

echo "DeskWidgets installed in $PREFIX. Next: deskwidgets setup && deskwidgets   (deskwidgets doctor checks the dependencies)"
case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) echo "note: add $PREFIX/bin to your PATH" ;; esac
