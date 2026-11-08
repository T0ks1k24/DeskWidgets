#!/bin/sh
# Builds dist/deskwidgets_<version>_<arch>.deb with dpkg-deb (no debhelper needed).
# The package is one executable (widgets, fonts and icons are inside it) plus the menu entry, the icon
# and the udev rule for wireless mice. Each user then opens "DeskWidgets" from the app menu.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/Cargo.toml" | head -n 1)
ARCH=$(dpkg --print-architecture)
APP_ID=io.github.toksik.DeskWidgets
BUILD="$ROOT/build/deb"
PKG="$BUILD/deskwidgets_${VERSION}_${ARCH}"

(cd "$ROOT" && cargo build --release --locked)

rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$ROOT/dist" "$PKG/usr/bin" "$PKG/usr/share/applications" \
    "$PKG/usr/share/icons/hicolor/scalable/apps" "$PKG/usr/share/doc/deskwidgets" "$PKG/lib/udev/rules.d"

install -m 755 "$ROOT/target/release/deskwidgets" "$PKG/usr/bin/deskwidgets"
cp "$ROOT/assets/icons/$APP_ID.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/"
cp "$ROOT/packaging/linux/$APP_ID.Gallery.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/assets/udev/70-deskwidgets-hid.rules" "$PKG/lib/udev/rules.d/"

# docs in the Debian place
cp "$ROOT/README.md" "$PKG/usr/share/doc/deskwidgets/"
cp "$ROOT/LICENSE" "$PKG/usr/share/doc/deskwidgets/copyright"
printf '\nThe bundled icon font (Material Design Icons) has its own license, see\nassets/fonts/LICENSE-MaterialDesignIcons.txt in the source, copied below.\n\n' \
    >> "$PKG/usr/share/doc/deskwidgets/copyright"
cat "$ROOT/assets/fonts/LICENSE-MaterialDesignIcons.txt" >> "$PKG/usr/share/doc/deskwidgets/copyright"
gzip -9n -c "$ROOT/CHANGELOG.md" > "$PKG/usr/share/doc/deskwidgets/changelog.gz"

SIZE=$(du -sk "$PKG/usr" "$PKG/lib" | awk '{s += $1} END {print s}')
sed -e "s/@VERSION@/$VERSION/" -e "s/@SIZE@/$SIZE/" -e "s/@ARCH@/$ARCH/" "$ROOT/packaging/control.in" > "$PKG/DEBIAN/control"
cp "$ROOT/packaging/postinst" "$ROOT/packaging/postrm" "$PKG/DEBIAN/"
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"

# predictable permissions regardless of the umask of whoever builds it
chmod 755 "$PKG" "$PKG/DEBIAN"
find "$PKG/usr" "$PKG/lib" -type d -exec chmod 755 {} +
find "$PKG/usr" "$PKG/lib" -type f -exec chmod 644 {} +
chmod 755 "$PKG/usr/bin/deskwidgets"

fakeroot dpkg-deb --build -Zxz "$PKG" "$ROOT/dist/" >/dev/null
echo "$ROOT/dist/deskwidgets_${VERSION}_${ARCH}.deb"
