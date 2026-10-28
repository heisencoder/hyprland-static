# hyprland-static

Builds a recent [Hyprland](https://github.com/hyprwm/Hyprland) for an Ubuntu
LTS release (currently **26.04**, which ships Hyprland 0.53) and packages it as
a `.deb`.

Hyprland's own libraries are newer than the LTS release has, so they're built
from source and **linked statically**: hyprutils, hyprlang, hyprwire,
hyprgraphics, hyprcursor and aquamarine.  Everything else (Mesa, libinput,
libdrm, Wayland, cairo, …) comes from Ubuntu and stays dynamic, so it keeps
getting Ubuntu's security and driver updates.

The package installs into `/opt/hyprland-static` and doesn't replace or
conflict with Ubuntu's `hyprland` package.  The login screen gets a
**Hyprland (static)** entry, and Ubuntu's Hyprland stays available as a
fallback session.

## Installing

Download the `.deb` from the [releases](../../releases) page and run:

    sudo apt install ./hyprland-static_*.deb

Then log out and pick **Hyprland (static)** at the login screen.  Remove it
with `sudo apt remove hyprland-static`.

The session launcher puts `/opt/hyprland-static/bin` first in `PATH`, so
`hyprctl` in scripts and keybindings matches the running Hyprland.

Not included: `hyprpm` and the plugin headers.  Plugins expect a dynamically
linked Hyprland.

Since 0.57, Hyprland reads only `~/.config/hypr/hyprland.lua`, not
`hyprland.conf`, and writes a default `hyprland.lua` if there's none.
Ubuntu's Hyprland (0.53) still reads `hyprland.conf`, so keep both files if
you use both sessions.

## Building

    sudo apt install $(cat build-deps.txt)
    git submodule update --init --recursive
    ./build.sh

`./build.sh` builds everything, runs the tests and writes the `.deb` to
`out/`.  `./build.sh <step>...` runs single steps (see the end of
`build.sh`); logs go to `logs/`.

| Path | What |
|---|---|
| `src/` | upstream projects as git submodules, pinned to release tags |
| `packaging/` | session launcher, login entry, Debian control template |
| `deps/`, `build/`, `stage/`, `out/`, `logs/` | build output (ignored by git) |

### Compiler and clean source

`build.sh` compiles with **GCC 16** (`gcc-16`/`g++-16`), Ubuntu 26.04's
non-default compiler, a pre-release snapshot.  Hyprland needs its C++
library (`std::ranges::starts_with`, which GCC 15 lacks), and with it
Hyprland builds from **unmodified upstream source**: `Hyprland --version`
reports `clean`, and the build's checks fail if it doesn't.  Ubuntu 26.04's
C++ runtime (`libstdc++6`, installed everywhere) already comes from the same
GCC 16 build.

`--version` also names the branch Hyprland was built from.  `.gitmodules`
records upstream's release branch for the pinned commit, and `build.sh`
checks the commit out on a local branch of that name.
`scripts/update_deps.py` sets it only when exactly one upstream branch
(other than the default branch) points at that commit, and removes it
otherwise; the name is never derived from the version.  Without it, the
branch in `--version` is empty.  Upstream makes such branches (`vX.Y.Z-b`)
for point releases, such as `v0.56.2-b` for 0.56.2; a `.0` release like
0.57.0 has none.

### Workarounds in build.sh

These change the libraries' build setup, not their code; the release notes
list them too.

- **Static libraries:** each library's CMakeLists hardcodes `SHARED`;
  build.sh switches it to `STATIC` and adds the missing `Requires:` lines to
  the libraries' `.pc` files.
- **wayland-protocols:** Ubuntu 26.04's wayland-scanner (1.24) rejects some
  newer protocol files in `--strict` mode.  Only the optional C enum headers
  use it, so build.sh drops the flag.
- **Duplicate protocol tables:** aquamarine and Hyprland both define
  `wl_*_interface` / `xdg_*_interface` from the same XML.  build.sh makes
  aquamarine's copies local with objcopy (with shared libraries, Hyprland's
  copies win at runtime anyway).
- **systemd user unit:** Hyprland 0.57 runs `systemctl --user start
  hyprland-session.target` when it starts (`HYPRLAND_NO_SD_TARGET=1` turns
  this off), and installs the unit in `${CMAKE_INSTALL_LIBDIR}/systemd/user`.
  That reaches systemd's search path only where LIBDIR is plain `lib` under
  `/usr`, as on Arch.  On Ubuntu a `/usr` build puts it in
  `/usr/lib/x86_64-linux-gnu/systemd/user`, and this build in
  `/opt/hyprland-static/lib/systemd/user`; systemd searches neither.
  build.sh moves it to `/usr/lib/systemd/user`.  Ubuntu 26.04's hyprland
  package doesn't ship one, so they don't conflict.

hyprland-protocols is built from `src/` too: Hyprland needs a newer one
than Ubuntu 26.04's (0.7.0).

## Tests

`./build.sh` runs:

- unit tests (unless `WITH_TESTS=0`), each project in a separate Debug
  build (their test builds add sanitizers and coverage instrumentation that
  the release build mustn't have): hyprutils, hyprlang, hyprwire,
  hyprgraphics and aquamarine's own tests, and Hyprland's
  `hyprland_gtests`;
- checks on the staged binaries: `Hyprland --version` reports the pinned
  version and release tag, `clean` source and, if `.gitmodules` records
  one, the release branch; `Hyprland`, `hyprctl`, `start-hyprland` and
  `hyprland-static-session` are installed; the first three link no
  Hyprland library dynamically and have no unresolved shared libraries; and
  `Hyprland --verify-config` accepts `packaging/test.lua`.

Not run, because build machines lack what they need:

- hyprcursor's tests (an installed hyprcursor theme);
- aquamarine's `simpleWindow` test (a running Wayland compositor);
- Hyprland's compositor tests, `hyprtester` (a GPU; not built).

glaze's and hyprwayland-scanner's tests aren't built either.

## Dependency updates

`.github/workflows/update-deps.yml` runs daily (and on demand from the
Actions tab).  It runs `scripts/update_deps.py`, which moves each submodule
in `src/` to its newest upstream release tag:

- Hyprland first, then the rest;
- glaze within the major versions the new Hyprland's CMakeLists accepts,
  when it gives a range (0.56 had `find_package(glaze 7...<8)`).  0.57's
  `find_package(glaze QUIET)` gives none, so glaze moves to its newest
  release (Hyprland's own fallback download is v7.2.0);
- wayland-protocols from freedesktop's GitLab, the rest from GitHub.

If anything changed, it pushes the branch `deps/update`, opens (or
refreshes) a pull request listing the new versions and the next release tag,
asks `heisencoder` to review it (and assigns it to them), and approves the
PR's CI and Python checks, which otherwise wait for approval because the
workflow opened the PR.  Nothing is merged or released automatically.
Opening the PR needs the repository setting "Allow GitHub Actions to create
and approve pull requests" (Settings > Actions > General > Workflow
permissions).

For Hyprland it also records upstream's release branch in `.gitmodules` (see
"Compiler and clean source").

If a new Hyprland needs more than a version bump (a new build dependency, a
build setup change), push fixes to `deps/update`.  Once it has
commits from anyone but the bot, the workflow leaves that PR alone.

The GitHub Actions used by the workflows are kept up to date by Dependabot
(`.github/dependabot.yml`).  Dependabot can't do the submodules: it only
moves them to the newest commit, not the newest release.

To update by hand instead, on a clean checkout:

    python3 -m scripts.update_deps --out /tmp/update

## Releases

Release by pushing a tag:

    scripts/next-release.sh          # prints the next tag, e.g. "v0.57.0-2 2"
    git fetch origin
    git tag v0.57.0-2 origin/main
    git push origin v0.57.0-2

`.github/workflows/release.yml` then builds and tests the `.deb` and
publishes a GitHub **pre-release** with it attached.

Its release notes come from `scripts/release_notes.py` (the `notes` step of
`./build.sh`, writing `out/release-notes.md`).  They list the exact version
and commit of everything built in: the projects in `src/`, Ubuntu libraries
linked statically (with their package versions), and protocol definitions
compiled in, plus the compiler, the build adjustments and `Hyprland --version`.
GitHub's list of merged pull requests follows.  CI shows the same notes on
each run's summary page.

The static archives must match `STATIC_ARCHIVES` in
`scripts/release_notes.py`.  If a new Hyprland adds or drops a static
dependency, writing the notes and the build tests fail until that list (and,
for a new project, the tables after it) is updated.  Install and test it,
then promote it to a full release (Edit, untick "Set as a pre-release") so
it reaches other users.

Releases are immutable once published (a repository setting), so the
package has to be attached when the release is created.  Don't create
releases in GitHub's web UI; push a tag instead.

The tag must be the Hyprland submodule's version, optionally followed by
`-N`; anything else fails the release build:

| Tag | Package version |
|---|---|
| `v0.57.0` | `0.57.0-1` |
| `v0.57.0-2` | `0.57.0-2` |

Use `-N` for packaging, build or library updates with the same Hyprland
version.

## Testing builds before release

Every CI run uploads its `.deb` as an artifact.  To install the latest
successful build of a pull request or branch (needs the GitHub CLI, logged
in; run it from a clone of this repo):

    scripts/install-ci-build.sh 12      # PR #12
    scripts/install-ci-build.sh main

CI packages are versioned just below the next release, e.g. `0.57.0-2~ci57`
before `v0.57.0-2`.  Installing one upgrades from the last release, and the
release replaces it when you install that.

## Python scripts and tests

The scripts in `scripts/*.py` only need Ubuntu's `python3`.  Their checks
use [uv](https://docs.astral.sh/uv/) (`pyproject.toml`, `uv.lock`):

    uv run ruff format --check     # formatting
    uv run ruff check              # lint, including required type annotations
    uv run ty check                # type checking
    uv run pytest                  # unit tests; fails below 91% coverage

`.github/workflows/python.yml` runs these on every pull request and push to
`main` (the **Python checks** status check).

Tests marked `build` need a finished `./build.sh` and check the static
dependencies in Hyprland's actual link commands (`ninja -t commands`)
against `STATIC_ARCHIVES` and the release notes.  CI runs them after its
build:

    uv run pytest -m build --no-cov

## CI

`.github/workflows/ci.yml` runs `./build.sh` on pushes to `main` and on pull
requests, on GitHub's `ubuntu-26.04` runner, and uploads the `.deb` as an
artifact.  Caches: the build-tool `.deb` files (keyed on `build-deps.txt`)
and ccache.  Both are saved even when the build fails.

## License

The build scripts are BSD-3-Clause (see `LICENSE`).  The package bundles
Hyprland, hyprutils, hyprlang, hyprwire, hyprgraphics, hyprcursor,
aquamarine and glaze under their own licenses (BSD-3-Clause for the Hyprland
projects, MIT for glaze); the package's
`/usr/share/doc/hyprland-static/copyright` includes them all.
