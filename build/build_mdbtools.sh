#!/bin/bash
# Build mdbtools from source under MSYS2 (mingw64) and bundle mdb-export.exe +
# its full DLL closure into src/makosync/tools/.
#
# Why build from source: MSYS2 has no mdbtools package and there's no trustworthy
# prebuilt Windows binary. Manager mode shells out to mdb-export; mdbtools reads
# the raw Jet file, so it ignores the Hy-Tek database password the ACE/ODBC
# driver can't open, and reads both Meet and Team Manager databases.
#
# Invoked by build/fetch_mdbtools.ps1 (which ensures MSYS2 + LF line endings).
set +e
export MSYSTEM=MINGW64
export PATH=/mingw64/bin:/usr/bin:$PATH
# aclocal must see mingw's pkg.m4, else configure: "pkg-config m4 macros... no".
export ACLOCAL_PATH="/mingw64/share/aclocal:/usr/share/aclocal"
export PKG_CONFIG="/mingw64/bin/pkg-config"
export PKG_CONFIG_PATH="/mingw64/lib/pkgconfig:/mingw64/share/pkgconfig"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$SCRIPT_DIR/.." && pwd)
DEST="$REPO/src/makosync/tools"
BUILD=/c/mdbtools-build
exec > "$REPO/_mdbbuild.log" 2>&1

echo "STEP deps"
pacman -Sy --noconfirm
pacman -S --needed --noconfirm autoconf automake libtool make git base-devel \
  mingw-w64-x86_64-gcc mingw-w64-x86_64-glib2 mingw-w64-x86_64-pkgconf \
  mingw-w64-x86_64-libiconv mingw-w64-x86_64-readline

echo "STEP build"
rm -rf "$BUILD"
git clone --depth 1 https://github.com/mdbtools/mdbtools.git "$BUILD"
cd "$BUILD" || { echo "CLONE/CD FAILED"; exit 1; }
autoreconf -i -f
./configure --disable-man --prefix=/mingw64
make -j4
# Fallback: build just the binary we need if some other util fails to compile.
make -C src/util mdb-export.exe || true

EXE=$(find "$BUILD" -name 'mdb-export.exe' -path '*.libs*' | head -1)
echo "EXE=$EXE"
if [ -z "$EXE" ] || [ ! -f "$EXE" ]; then echo "NO EXE PRODUCED"; exit 1; fi

mkdir -p "$DEST"
cp -f "$EXE" "$DEST/mdb-export.exe"
# mdb-export links mdbtools' own libmdb-3.dll.
LIBMDB=$(find "$BUILD" -name 'libmdb-3.dll' -path '*.libs*' | head -1)
[ -n "$LIBMDB" ] && cp -f "$LIBMDB" "$DEST/"

# Copy the transitive mingw64 DLL closure (iterate to a fixed point).
copy_deps() {
  ldd "$1" 2>/dev/null | grep -i 'mingw64' | grep -oiE '[a-z0-9_.+-]+\.dll' | sort -u | while read -r b; do
    if [ -f "/mingw64/bin/$b" ] && [ ! -f "$DEST/$b" ]; then cp -f "/mingw64/bin/$b" "$DEST/"; fi
  done
}
for pass in 1 2 3 4; do
  copy_deps "$DEST/mdb-export.exe"
  for f in "$DEST"/*.dll; do copy_deps "$f"; done
done

echo "STEP verify (must print mdbtools version)"
cd "$DEST" && ./mdb-export.exe --version
echo "rc=$?"
ls -la "$DEST"
