"""Write release notes listing exactly what a build put into its binaries.

Run after `./build.sh` builds Hyprland (its `notes` step does).  The notes
cover:

- the projects built from source in src/, with their upstream release tag
  and commit, and which binaries contain them;
- Ubuntu libraries linked statically, with the Ubuntu package and version
  that provided them;
- protocol definitions from Ubuntu compiled into Hyprland;
- the build environment, build adjustments, and `Hyprland --version`.

The static archives found in the build's link commands must match
STATIC_ARCHIVES below.  If a new Hyprland adds or drops one, this fails
until STATIC_ARCHIVES (and, for a new project, the tables below it) are
updated, so the notes never silently miss a dependency.

Usage: python3 -m scripts.release_notes [--revision N] [--out FILE]
"""

import argparse
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from scripts.common import ROOT, Submodule, git, remote_tags, run, submodules

BINARIES = ["Hyprland", "hyprctl", "start-hyprland"]

# The static archives each binary is linked with, by file name.  Excludes
# Hyprland's own internal library (libhyprland_lib.a) and glibc's empty
# compatibility stubs (librt.a and similar), which contain no code.
STATIC_ARCHIVES: dict[str, set[str]] = {
    "Hyprland": {
        # Built from src/
        "libaquamarine.a",
        "libhyprcursor.a",
        "libhyprgraphics.a",
        "libhyprlang.a",
        "libhyprutils.a",
        # From Ubuntu's glslang-dev
        "libglslang.a",
        "libglslang-default-resource-limits.a",
        "libSPIRV.a",
        # From Ubuntu's liblcms2-dev
        "liblcms2_fast_float.a",
        "liblcms2_threaded.a",
    },
    "hyprctl": {"libhyprutils.a", "libhyprwire.a"},
    "start-hyprland": {"libhyprutils.a"},
}

# How each project in src/ ends up in the binaries.  Static libraries are
# matched to binaries from the link commands; COMPILED_INTO covers the rest.
HOW = {
    "Hyprland": "the compositor itself",
    "hyprutils": "static library",
    "hyprlang": "static library",
    "hyprwire": "static library",
    "hyprgraphics": "static library",
    "hyprcursor": "static library",
    "aquamarine": "static library",
    "glaze": "header-only library, compiled in",
    "wayland-protocols": "protocol definitions, compiled in",
    "hyprland-protocols": "protocol definitions, compiled in",
    "hyprwayland-scanner": "code generator for the protocols (build tool)",
}
COMPILED_INTO = {
    "Hyprland": ["Hyprland", "hyprctl", "start-hyprland"],
    "glaze": ["Hyprland", "start-hyprland"],
    "wayland-protocols": ["Hyprland"],
    "hyprland-protocols": ["Hyprland"],
    "hyprwayland-scanner": [],
}

# What build.sh changes in the other projects' build setup (see build.sh).
BUILD_ADJUSTMENTS = [
    "hyprutils, hyprlang, hyprwire, hyprgraphics, hyprcursor, aquamarine: "
    "built as static instead of shared libraries",
    "aquamarine: its copies of the Wayland protocol tables made local, "
    "so they don't collide with Hyprland's identical ones",
    "wayland-protocols: optional C enum headers generated without `--strict`, "
    "which Ubuntu 26.04's wayland-scanner rejects for some newer files",
    "Hyprland: its systemd user unit (`hyprland-session.target`) installed in "
    "`/usr/lib/systemd/user`, where systemd finds it, instead of under `/opt/hyprland-static`",
]

# Ubuntu packages whose files are compiled into Hyprland at build time.
UBUNTU_COMPILED_IN = {
    "libwayland-dev": "core Wayland protocol definitions",
}

# An ar archive with no members is just its 8-byte magic ("!<arch>\n").
EMPTY_ARCHIVE_SIZE = 8


def link_archives(ninja: str) -> dict[str, list[str]]:
    """{binary: static archive paths} from the link commands in build.ninja.

    Paths are as written in build.ninja; Hyprland's internal library is
    relative (libhyprland_lib.a), everything else absolute.
    """
    result: dict[str, list[str]] = {}
    for binary in BINARIES:
        m = re.search(
            rf"^build (?:\S*/)?{re.escape(binary)}: CXX_EXECUTABLE_LINKER.*\n((?:  .*\n)+)",
            ninja,
            re.MULTILINE,
        )
        libs = re.search(r"LINK_LIBRARIES = (.*)", m.group(1)) if m else None
        result[binary] = re.findall(r"(\S+\.a)(?=\s|$)", libs.group(1)) if libs else []
    return result


def external_archives(paths: list[str]) -> list[str]:
    """Drop Hyprland's internal library and empty archives."""
    return [p for p in paths if p.startswith("/") and Path(p).stat().st_size > EMPTY_ARCHIVE_SIZE]


def check_static_archives(actual: dict[str, list[str]]) -> list[str]:
    """Differences between the linked archives and STATIC_ARCHIVES."""
    problems = []
    for binary in BINARIES:
        found = {Path(p).name for p in actual.get(binary, [])}
        expected = STATIC_ARCHIVES[binary]
        if added := sorted(found - expected):
            problems.append(f"{binary}: newly linked statically: {', '.join(added)}")
        if dropped := sorted(expected - found):
            problems.append(f"{binary}: no longer linked statically: {', '.join(dropped)}")
    return problems


def upstream_tag(url: str, commit: str) -> str | None:
    """The upstream tag for a commit (checkouts have no tags in CI)."""
    return next((t for t, sha in remote_tags(url).items() if sha == commit), None)


def dpkg_version(package: str) -> str:
    return run("dpkg-query", "-W", "-f=${Version}", package)


def dpkg_owner(path: str) -> str:
    return run("dpkg", "-S", path).split(":")[0]


@dataclass
class Notes:
    hyprland_version: str
    revision: str
    distro: str
    compiler: str  # the C++ compiler's name, e.g. "g++-16"
    compiler_version: str  # its version, e.g. "16.0.1"
    compiler_package: str  # the Ubuntu package and version that provided it
    version_output: str
    source_rows: list[str] = field(default_factory=list)
    ubuntu_rows: list[str] = field(default_factory=list)
    compiled_in_rows: list[str] = field(default_factory=list)


def source_rows(mods: list[Submodule], archives: dict[str, list[str]], root: Path) -> list[str]:
    """Table rows for the projects in src/."""
    in_binaries: dict[str, list[str]] = {}
    for binary, paths in archives.items():
        for path in paths:
            if m := re.search(r"/deps/lib/lib(.+)\.a$", path):
                in_binaries.setdefault(m.group(1), []).append(f"`{binary}`")
    rows = []
    for mod in mods:
        commit = git("rev-parse", "HEAD", cwd=root / mod.path)
        tag = upstream_tag(mod.url, commit) or "(untagged)"
        how = HOW.get(mod.name, "")
        if mod.name in COMPILED_INTO:
            where = ", ".join(f"`{b}`" for b in COMPILED_INTO[mod.name]) or "(build only)"
        else:
            where = ", ".join(in_binaries.get(mod.name, [])) or "(not linked)"
        project = mod.url.removesuffix(".git")
        rows.append(f"| [{mod.name}]({project}) | {tag} | `{commit[:12]}` | {how} | {where} |")
    return rows


def ubuntu_rows(archives: dict[str, list[str]], root: Path) -> list[str]:
    """Table rows for Ubuntu archives linked statically, grouped by package."""
    by_package: dict[str, tuple[set[str], set[str]]] = {}
    for binary, paths in archives.items():
        for path in paths:
            if path.startswith(str(root)):
                continue
            libs, bins = by_package.setdefault(dpkg_owner(path), (set(), set()))
            libs.add(Path(path).name)
            bins.add(f"`{binary}`")
    return [
        f"| `{pkg}` | {dpkg_version(pkg)} | {', '.join(sorted(libs))} | {', '.join(sorted(bins))} |"
        for pkg, (libs, bins) in sorted(by_package.items())
    ]


def source_state(version_output: str) -> str:
    """Whether Hyprland was built from unmodified upstream source, per its --version."""
    if re.search(r" at commit \S+ clean ", version_output):
        return "unmodified upstream source (`--version` reports `clean`)"
    return "modified (`--version` doesn't report `clean`)"


def render(notes: Notes) -> str:
    deb = f"hyprland-static_{notes.hyprland_version}-{notes.revision}_amd64.deb"
    return f"""\
Hyprland **{notes.hyprland_version}** for **{notes.distro}**, \
package version `{notes.hyprland_version}-{notes.revision}`.

## Install

    sudo apt install ./{deb}

Then pick **Hyprland (static)** at the login screen.  It installs into
`/opt/hyprland-static`, next to Ubuntu's `hyprland` package.

## Built from source

| Project | Version | Commit | How it's included | In |
|---|---|---|---|---|
{chr(10).join(notes.source_rows)}

## Ubuntu libraries linked statically

| Ubuntu package | Version | Archives | In |
|---|---|---|---|
{chr(10).join(notes.ubuntu_rows) or "| (none) | | | |"}

## Compiled in from Ubuntu packages

| Ubuntu package | Version | What |
|---|---|---|
{chr(10).join(notes.compiled_in_rows)}

Everything else (Mesa, libinput, libdrm, Wayland, cairo, …) is linked
dynamically from Ubuntu; see the package's `Depends`.

## Build

- {notes.distro}
- Compiler: `{notes.compiler}` {notes.compiler_version} (Ubuntu package {notes.compiler_package})
- Hyprland: {source_state(notes.version_output)}
- Build adjustments to the libraries (no code changes):
{chr(10).join(f"  - {a}" for a in BUILD_ADJUSTMENTS)}

<details><summary><code>Hyprland --version</code></summary>

```
{notes.version_output}
```

"system has unknown" is expected: Hyprland looks for separately installed
copies of its libraries, but this build has them built in.

</details>
"""


def cmake_cache_value(cache: Path, name: str) -> str:
    """A variable from a CMakeCache.txt (lines like NAME:TYPE=value)."""
    for line in cache.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.split(":")[0] == name:
            return value
    raise KeyError(f"{name} not in {cache}")


def distro_name(os_release: Path = Path("/etc/os-release")) -> str:
    fields = dict(line.split("=", 1) for line in os_release.read_text().splitlines() if "=" in line)
    return fields.get("PRETTY_NAME", "unknown").strip('"')


def hyprland_version_output(binary: Path) -> str:
    """`Hyprland --version`, which wants a runtime directory even for that."""
    with tempfile.TemporaryDirectory() as runtime:
        return run(binary, "--version", env=dict(os.environ, XDG_RUNTIME_DIR=runtime))


def collect(revision: str, root: Path = ROOT) -> Notes:
    """Gather everything the notes report.  Exits if the static archives changed."""
    archives = {
        binary: external_archives(paths)
        for binary, paths in link_archives(
            (root / "build/Hyprland/build.ninja").read_text()
        ).items()
    }
    if problems := check_static_archives(archives):
        sys.exit(
            "The static dependencies changed; update STATIC_ARCHIVES in "
            "scripts/release_notes.py (and its tables, for a new project):\n  "
            + "\n  ".join(problems)
        )
    compiler = cmake_cache_value(root / "build/Hyprland/CMakeCache.txt", "CMAKE_CXX_COMPILER")
    package = dpkg_owner(compiler)
    return Notes(
        hyprland_version=(root / "src/Hyprland/VERSION").read_text().strip(),
        revision=revision,
        distro=distro_name(),
        compiler=Path(compiler).name,
        compiler_version=run(compiler, "-dumpfullversion"),
        compiler_package=f"`{package}` {dpkg_version(package)}",
        version_output=hyprland_version_output(root / "stage/opt/hyprland-static/bin/Hyprland"),
        source_rows=source_rows(submodules(root), archives, root),
        ubuntu_rows=ubuntu_rows(archives, root),
        compiled_in_rows=[
            f"| `{pkg}` | {dpkg_version(pkg)} | {what} |"
            for pkg, what in UBUNTU_COMPILED_IN.items()
        ],
    )


def main(argv: list[str] | None = None, root: Path = ROOT) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--revision",
        default=os.environ.get("DEB_REVISION", "1"),
        help="Debian revision of the package (default: $DEB_REVISION or 1)",
    )
    parser.add_argument("--out", type=Path, default=root / "out/release-notes.md")
    args = parser.parse_args(argv)

    notes = render(collect(args.revision, root))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(notes)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
