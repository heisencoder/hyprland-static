#!/bin/bash
# Build Hyprland for an Ubuntu LTS release (currently 26.04) with the Hyprland
# ecosystem libraries (hyprutils, hyprlang, hyprwire, hyprgraphics, hyprcursor,
# aquamarine) linked statically.  Everything else comes from Ubuntu and stays
# dynamic.  The result installs into /opt/hyprland-static, next to Ubuntu's
# own hyprland package.
#
#   src/     git submodules, pinned to upstream release tags
#   deps/    build-time prefix for the static libraries and tools
#   build/   CMake/Meson build directories
#   stage/   DESTDIR for the final install (what goes into the .deb)
#   out/     the .deb and its release notes
#   logs/    one log per step
#
# Usage: ./build.sh [step...]   (default: build, test and package)
#
# Environment:
#   JOBS          parallel jobs (default: nproc)
#   WITH_TESTS    1 to build and run the unit tests (default: 1)
#   DEB_REVISION  Debian revision of the package version (default: 1)
#   CC, CXX       compilers (default: gcc-16, g++-16)
set -euo pipefail

TOP=$(cd "$(dirname "$0")" && pwd)
SRC=$TOP/src
DEPS=$TOP/deps
BUILD=$TOP/build
STAGE=$TOP/stage
OUT=$TOP/out
LOGS=$TOP/logs
NAME=hyprland-static
FINAL_PREFIX=/opt/$NAME
JOBS=${JOBS:-$(nproc)}
WITH_TESTS=${WITH_TESTS:-1}
DEB_REVISION=${DEB_REVISION:-1}

# GCC 16, Ubuntu 26.04's non-default compiler (a pre-release snapshot,
# package g++-16).  Hyprland needs its C++ library (std::ranges::starts_with),
# and with it Hyprland builds from unmodified upstream source.  Ubuntu 26.04's
# C++ runtime (libstdc++6) already comes from the same GCC 16 build.
export CC=${CC:-gcc-16} CXX=${CXX:-g++-16}

export PKG_CONFIG_PATH=$DEPS/lib/pkgconfig:$DEPS/share/pkgconfig
export PATH=$DEPS/bin:$PATH
export CMAKE_PREFIX_PATH=$DEPS

CMAKE_LAUNCHER=()
if command -v ccache >/dev/null; then
  CMAKE_LAUNCHER=(-DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache)
fi

mkdir -p "$DEPS" "$BUILD" "$LOGS"

# The ecosystem libraries hardcode SHARED; build them STATIC instead.
make_static() {
  sed -i -E 's/add_library\(([a-z]+) SHARED/add_library(\1 STATIC/' "$SRC/$1/CMakeLists.txt"
}

# Their .pc files don't list their own dependencies (fine for shared
# libraries, not for static ones), so add them.
add_requires() {
  local pc=$DEPS/lib/pkgconfig/$1.pc
  shift
  grep -q '^Requires:' "$pc" || echo "Requires: $*" >> "$pc"
}

cmake_lib() {
  local name=$1
  shift
  cmake -S "$SRC/$name" -B "$BUILD/$name" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$DEPS" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DBUILD_TESTING=OFF \
    "${CMAKE_LAUNCHER[@]}" \
    "$@"
  cmake --build "$BUILD/$name" -j "$JOBS"
  cmake --install "$BUILD/$name"
}

# Build a project's unit tests in a separate Debug build and run them.  Most
# Hyprland projects only enable tests in Debug builds, and their test builds
# add sanitizers or coverage instrumentation that the release build mustn't
# have.  Some compile the library itself with --coverage; as a static library
# it doesn't bring the coverage runtime along, so every executable links it.
cmake_test() {
  local name=$1
  shift
  cmake -S "$SRC/$name" -B "$BUILD/$name-tests" -G Ninja \
    -DCMAKE_BUILD_TYPE=Debug \
    -DBUILD_TESTING=ON \
    -DCMAKE_EXE_LINKER_FLAGS=--coverage \
    "${CMAKE_LAUNCHER[@]}" \
    "$@"
  cmake --build "$BUILD/$name-tests" -j "$JOBS" ${TEST_TARGET:+--target "$TEST_TARGET"}
  ctest --test-dir "$BUILD/$name-tests" --output-on-failure -j "$JOBS" \
    ${CTEST_EXCLUDE:+--exclude-regex "$CTEST_EXCLUDE"}
}

# From Hyprland's VERSION file: CI checks out submodules without their tags.
hyprland_version() {
  cat "$SRC/Hyprland/VERSION"
}

# Hyprland's --version reports `git describe --tags` of its source.
# Submodules are checked out without tags (as in CI), so fetch this
# version's tag if it's missing.
fetch_hyprland_tag() {
  local tag
  tag=v$(hyprland_version)
  git -C "$SRC/Hyprland" rev-parse -q --verify "refs/tags/$tag" >/dev/null ||
    git -C "$SRC/Hyprland" fetch --depth 1 origin "refs/tags/$tag:refs/tags/$tag"
}

### Build-time dependencies ###

step_wayland_protocols() {
  # Ubuntu 26.04's wayland-scanner (1.24) rejects some newer protocol files
  # in --strict mode.  That only affects the optional C enum headers; the XML
  # files Hyprland uses install unchanged.
  sed -i "/'--strict',/d" "$SRC/wayland-protocols/include/wayland-protocols/meson.build"
  meson setup --reconfigure "$BUILD/wayland-protocols" "$SRC/wayland-protocols" \
    --prefix="$DEPS" -Dtests=false
  meson install -C "$BUILD/wayland-protocols"
}

# Hyprland needs a newer hyprland-protocols than Ubuntu 26.04 has (0.7.0).
step_hyprland_protocols() { cmake_lib hyprland-protocols; }

step_glaze() {
  cmake_lib glaze -Dglaze_DEVELOPER_MODE=OFF \
    -Dglaze_BUILD_EXAMPLES=OFF -Dglaze_ENABLE_FUZZING=OFF
}

step_hyprwayland_scanner() { cmake_lib hyprwayland-scanner; }

### Static libraries ###

step_hyprutils() {
  make_static hyprutils
  cmake_lib hyprutils
  add_requires hyprutils pixman-1
}

step_hyprlang() {
  make_static hyprlang
  cmake_lib hyprlang
  add_requires hyprlang hyprutils
}

step_hyprwire() {
  make_static hyprwire
  cmake_lib hyprwire
  add_requires hyprwire hyprutils libffi
}

step_hyprgraphics() {
  make_static hyprgraphics
  cmake_lib hyprgraphics
  add_requires hyprgraphics libdrm pixman-1 cairo pangocairo hyprutils libjpeg \
    libwebp libmagic libpng librsvg-2.0 libjxl libjxl_cms libjxl_threads libheif
}

step_hyprcursor() {
  make_static hyprcursor
  cmake_lib hyprcursor
  add_requires hyprcursor hyprlang libzip cairo librsvg-2.0 tomlplusplus
}

step_aquamarine() {
  make_static aquamarine
  cmake_lib aquamarine
  add_requires aquamarine libseat libinput wayland-client hyprutils pixman-1 \
    libdrm gbm libudev libdisplay-info egl opengl
  # aquamarine and Hyprland each generate the same Wayland protocol tables
  # (wl_shm_interface, xdg_wm_base_interface, ...) from the same XML files.
  # As a shared library that's fine (Hyprland's copies win at runtime); linked
  # statically the duplicates collide.  Make aquamarine's copies local.
  local syms
  syms=$(nm --defined-only "$DEPS/lib/libaquamarine.a" |
    awk '$3 ~ /_interface$/ && $2 ~ /[DdRr]/ {print "--localize-symbol=" $3}' | sort -u)
  objcopy $syms "$DEPS/lib/libaquamarine.a"
}

### Hyprland ###

# Put Hyprland on upstream's release branch, if .gitmodules names one (set
# by scripts/update_deps.py only when exactly one upstream branch is at the
# pinned commit), so --version reports it.  Only when the checkout is the
# pinned commit; otherwise the branch stays empty.
checkout_hyprland_branch() {
  local branch
  branch=$(git -C "$TOP" config -f .gitmodules --get submodule.src/Hyprland.branch || true)
  [ -n "$branch" ] || return 0
  if [ "$(git -C "$TOP" submodule status src/Hyprland | cut -c1)" != " " ]; then
    echo "src/Hyprland isn't at its pinned commit; leaving the branch empty" >&2
    return 0
  fi
  git -C "$SRC/Hyprland" checkout -q -B "$branch"
}

step_hyprland() {
  fetch_hyprland_tag
  checkout_hyprland_branch
  cmake -S "$SRC/Hyprland" -B "$BUILD/Hyprland" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$FINAL_PREFIX" \
    -DBUILD_TESTING=OFF \
    "${CMAKE_LAUNCHER[@]}"
  cmake --build "$BUILD/Hyprland" -j "$JOBS"
  rm -rf "$STAGE"
  DESTDIR="$STAGE" cmake --install "$BUILD/Hyprland"
}

# Session launcher and login-screen entry; drop what a static build can't
# use (plugin headers and hyprpm, which builds plugins against them).  Move
# the systemd user unit that Hyprland starts (hyprland-session.target) to
# where systemd looks for it.
step_extras() {
  local opt=$STAGE$FINAL_PREFIX
  install -D -m 755 "$TOP/packaging/hyprland-static-session" "$opt/bin/hyprland-static-session"
  install -D -m 644 "$TOP/packaging/hyprland-static.desktop" \
    "$STAGE/usr/share/wayland-sessions/hyprland-static.desktop"
  if [ -d "$opt/lib/systemd" ]; then
    mkdir -p "$STAGE/usr/lib"
    mv "$opt/lib/systemd" "$STAGE/usr/lib/systemd"
  fi
  # Nothing else belongs there; fail if a new release installs something.
  [ ! -e "$opt/lib" ] || rmdir "$opt/lib"
  rm -rf "$opt/include" "$opt/share/pkgconfig" "$opt/share/wayland-sessions" \
    "$opt/bin/hyprpm" "$opt/share/bash-completion/completions/hyprpm" \
    "$opt/share/fish/vendor_completions.d/hyprpm.fish" "$opt/share/zsh/site-functions/_hyprpm"
}

### Tests ###

# Unit tests of the static libraries.  Skipped because build machines lack
# what they need: hyprcursor's tests (an installed hyprcursor theme) and
# aquamarine's simpleWindow (a running Wayland compositor).
step_test_libs() {
  local lib
  for lib in hyprutils hyprlang hyprwire hyprgraphics; do
    echo "--- $lib"
    cmake_test "$lib"
  done
  echo "--- aquamarine"
  CTEST_EXCLUDE=simpleWindow cmake_test aquamarine
}

# Hyprland's own unit tests (hyprland_gtests).  Its full compositor tests
# (hyprtester) need a GPU, so they aren't built.
step_test_hyprland() {
  TEST_TARGET=hyprland_gtests cmake_test Hyprland
}

# Check the staged binaries: right version, no dynamic Hyprland libraries,
# no missing libraries, and the session launcher's target exists.
step_test_stage() {
  local bin=$STAGE$FINAL_PREFIX/bin b
  # Hyprland wants a runtime directory even for --version; CI may not have one.
  XDG_RUNTIME_DIR=$(mktemp -d)
  export XDG_RUNTIME_DIR
  "$bin/Hyprland" --version | grep -q "^Hyprland $(hyprland_version) "
  "$bin/Hyprland" --version | grep -q "^Tag: v$(hyprland_version),"
  # Built from unmodified upstream source, and on upstream's release branch
  # when .gitmodules names one.
  "$bin/Hyprland" --version | grep -q " at commit [0-9a-f]* clean ("
  local branch
  branch=$(git -C "$TOP" config -f .gitmodules --get submodule.src/Hyprland.branch || true)
  if [ -n "$branch" ]; then
    "$bin/Hyprland" --version | grep -qF "built from branch $branch at commit"
  fi
  for b in Hyprland hyprctl start-hyprland hyprland-static-session; do
    [ -x "$bin/$b" ] || { echo "$b: missing" >&2; return 1; }
  done
  for b in Hyprland hyprctl start-hyprland; do
    if ldd "$bin/$b" | grep -E 'not found|libhypr|libaquamarine'; then
      echo "$b: unexpected dynamic libraries" >&2
      return 1
    fi
  done
  "$bin/Hyprland" --verify-config -c "$TOP/packaging/test.lua" | tail -1 | grep -q "config ok"
  rm -rf "$XDG_RUNTIME_DIR"
}

### Package ###

step_package() {
  local version arch pkg
  version=$(hyprland_version)-$DEB_REVISION
  arch=$(dpkg --print-architecture)
  pkg=$BUILD/deb
  rm -rf "$pkg"
  cp -a "$STAGE" "$pkg"

  install -D -m 644 "$TOP/packaging/copyright" "$pkg/usr/share/doc/$NAME/copyright"
  # The protocol projects are compiled in as code generated from their XML.
  local p licenses
  for p in Hyprland hyprutils hyprlang hyprwire hyprgraphics hyprcursor aquamarine glaze \
    hyprland-protocols wayland-protocols; do
    licenses=$(find "$SRC/$p" -maxdepth 1 -type f \( -name 'LICENSE*' -o -name 'COPYING*' \) | sort)
    [ -n "$licenses" ] || { echo "$p: no LICENSE or COPYING file" >&2; return 1; }
    {
      printf '\nFiles: bundled %s (commit %s)\n' "$p" "$(git -C "$SRC/$p" rev-parse --short=12 HEAD)"
      sed 's/^$/./; s/^/ /' $licenses
    } >> "$pkg/usr/share/doc/$NAME/copyright"
  done

  # Runtime dependencies, from the binaries' shared libraries.
  local shlibs bins
  bins=$(find "$pkg$FINAL_PREFIX/bin" -type f -perm -u+x -exec sh -c '[ "$(head -c4 "$1" | tail -c3)" = ELF ]' _ {} \; -print)
  mkdir -p "$BUILD/shlibs/debian"
  printf 'Source: %s\n\nPackage: %s\nArchitecture: any\n' "$NAME" "$NAME" > "$BUILD/shlibs/debian/control"
  shlibs=$(cd "$BUILD/shlibs" && dpkg-shlibdeps -O $bins 2>/dev/null | sed 's/^shlibs:Depends=//')

  mkdir -p "$pkg/DEBIAN"
  sed -e "s/@VERSION@/$version/" -e "s/@ARCH@/$arch/" -e "s/@DEPENDS@/$shlibs/" \
    -e "s/@SIZE@/$(du -sk "$pkg" | cut -f1)/" \
    "$TOP/packaging/control.in" > "$pkg/DEBIAN/control"

  mkdir -p "$OUT"
  dpkg-deb --root-owner-group -Zxz --build "$pkg" "$OUT/${NAME}_${version}_${arch}.deb"
  dpkg-deb --info "$OUT/${NAME}_${version}_${arch}.deb"
}

# Release notes listing exactly what went into the binaries (out/release-notes.md).
step_notes() {
  (cd "$TOP" && DEB_REVISION=$DEB_REVISION python3 -m scripts.release_notes --out "$OUT/release-notes.md")
}

BUILD_STEPS=(wayland_protocols hyprland_protocols glaze hyprwayland_scanner hyprutils
             hyprlang hyprwire hyprgraphics hyprcursor aquamarine hyprland extras)
TEST_STEPS=(test_stage)
[ "$WITH_TESTS" = 1 ] && TEST_STEPS+=(test_libs test_hyprland)
ALL=("${BUILD_STEPS[@]}" "${TEST_STEPS[@]}" package notes)
STEPS=("${@:-${ALL[@]}}")

for s in "${STEPS[@]}"; do
  echo "=== $s ($(date +%T))"
  # Run each step in a subshell with errexit, so a failing command stops the
  # step (bash ignores set -e inside a function called from an if).
  set +e
  (set -e; "step_$s") > "$LOGS/$s.log" 2>&1
  rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    echo "FAILED: $s (see $LOGS/$s.log)"
    tail -40 "$LOGS/$s.log"
    exit 1
  fi
done
echo "=== done ($(date +%T))"
