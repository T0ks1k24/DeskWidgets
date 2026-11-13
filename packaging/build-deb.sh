#!/bin/sh
# Builds dist/deskwidgets_<version>_all.deb with dpkg-deb (no debhelper needed).
# The Python package goes into the system's dist-packages, so the distribution's own
# python3-cairo, python3-gi ... are used. Each user then opens "DeskWidgets" from the app menu.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(sed -n "s/^VERSION = '\(.*\)'/\1/p" "$ROOT/deskwidgets/__init__.py")
APP_ID=io.github.toksik.DeskWidgets
BUILD="$ROOT/build/deb"
PKG="$BUILD/deskwidgets_${VERSION}_all"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$ROOT/dist" "$PKG/usr/bin" "$PKG/usr/lib/python3/dist-packages" \
    "$PKG/usr/share/applications" "$PKG/usr/share/icons/hicolor/scalable/apps" "$PKG/usr/share/doc/deskwidgets" \
    "$PKG/lib/udev/rules.d"

cp -r "$ROOT/deskwidgets" "$PKG/usr/lib/python3/dist-packages/"
find "$PKG/usr/lib/python3/dist-packages" -name '__pycache__' -type d -prune -exec rm -rf {} +
rm -rf "$PKG/usr/lib/python3/dist-packages/deskwidgets/assets/udev"

cat > "$PKG/usr/bin/deskwidgets" <<'LAUNCHER'
#!/usr/bin/python3
import sys

from deskwidgets.cli import main

sys.exit(main())
LAUNCHER

cp "$ROOT/deskwidgets/assets/icons/$APP_ID.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/"
cp "$ROOT/packaging/linux/$APP_ID.Gallery.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/deskwidgets/assets/udev/70-deskwidgets-hid.rules" "$PKG/lib/udev/rules.d/"

# docs in the Debian place
cp "$ROOT/README.md" "$PKG/usr/share/doc/deskwidgets/"
cp "$ROOT/LICENSE" "$PKG/usr/share/doc/deskwidgets/copyright"
printf '\nThe bundled icon font has its own license, see\n/usr/lib/python3/dist-packages/deskwidgets/assets/fonts/LICENSE-MaterialDesignIcons.txt\n' \
    >> "$PKG/usr/share/doc/deskwidgets/copyright"
gzip -9n -c "$ROOT/CHANGELOG.md" > "$PKG/usr/share/doc/deskwidgets/changelog.gz"

SIZE=$(du -sk "$PKG/usr" "$PKG/lib" | awk '{s += $1} END {print s}')
sed -e "s/@VERSION@/$VERSION/" -e "s/@SIZE@/$SIZE/" "$ROOT/packaging/control.in" > "$PKG/DEBIAN/control"
cp "$ROOT/packaging/postinst" "$ROOT/packaging/postrm" "$PKG/DEBIAN/"
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"

# predictable permissions regardless of the umask of whoever builds it
chmod 755 "$PKG" "$PKG/DEBIAN"
find "$PKG/usr" "$PKG/lib" -type d -exec chmod 755 {} +
find "$PKG/usr" "$PKG/lib" -type f -exec chmod 644 {} +
chmod 755 "$PKG/usr/bin/deskwidgets"

fakeroot dpkg-deb --build -Zxz "$PKG" "$ROOT/dist/" >/dev/null
echo "$ROOT/dist/deskwidgets_${VERSION}_all.deb"
