#!/bin/sh
# Builds dist/deskwidgets_<version>_all.deb with dpkg-deb (no debhelper needed).
# Installs into /usr: the launcher, /usr/share/deskwidgets, the app-menu entry, the icon
# and the GNOME Shell grid helper. Each user then runs `deskwidgets` (or opens "DeskWidgets").
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(sed -n "s/^VERSION = '\(.*\)'/\1/p" "$ROOT/deskwidgets/__init__.py")
BUILD="$ROOT/build/deb"
PKG="$BUILD/deskwidgets_${VERSION}_all"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$ROOT/dist"

DESTDIR="$PKG" "$ROOT/install.sh" --prefix /usr >/dev/null
# the launcher must use the system Python (python3-gi lives there)
sed -i '1s|.*|#!/usr/bin/python3|' "$PKG/usr/bin/deskwidgets"
# docs in the Debian place
mkdir -p "$PKG/usr/share/doc/deskwidgets"
cp "$ROOT/README.md" "$ROOT/CHANGELOG.md" "$PKG/usr/share/doc/deskwidgets/"
cp "$ROOT/LICENSE" "$PKG/usr/share/doc/deskwidgets/copyright"
printf '\nThe bundled icon font has its own license, see\n/usr/share/deskwidgets/fonts/LICENSE-MaterialDesignIcons.txt\n' \
    >> "$PKG/usr/share/doc/deskwidgets/copyright"
gzip -9n -c "$ROOT/CHANGELOG.md" > "$PKG/usr/share/doc/deskwidgets/changelog.gz"
rm -f "$PKG/usr/share/doc/deskwidgets/CHANGELOG.md"

SIZE=$(du -sk "$PKG/usr" | cut -f1)
sed -e "s/@VERSION@/$VERSION/" -e "s/@SIZE@/$SIZE/" "$ROOT/packaging/control.in" > "$PKG/DEBIAN/control"
cp "$ROOT/packaging/postinst" "$ROOT/packaging/postrm" "$PKG/DEBIAN/"
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"

rm -f "$PKG/usr/share/applications/mimeinfo.cache"
# predictable permissions regardless of the umask of whoever builds it
chmod 755 "$PKG" "$PKG/DEBIAN"
find "$PKG/usr" -type d -exec chmod 755 {} +
find "$PKG/usr" -type f -exec chmod 644 {} +
chmod 755 "$PKG/usr/bin/deskwidgets"
find "$PKG/usr/share/deskwidgets/widgets" \( -name '*.py' -o -name '*.sh' \) -exec chmod 755 {} +

fakeroot dpkg-deb --build -Zxz "$PKG" "$ROOT/dist/" >/dev/null
echo "$ROOT/dist/deskwidgets_${VERSION}_all.deb"
