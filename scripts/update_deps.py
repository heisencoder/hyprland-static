"""Move each submodule in src/ to its newest upstream release tag.

Hyprland is updated first, because its CMakeLists can limit the others:
glaze stays within the major versions Hyprland accepts when it names a range
(find_package(glaze 7...<8) up to 0.56; 0.57 names none).

Writes a PR title, a Markdown summary and a commit message into --out, and
prints "changed=true" or "changed=false" (for $GITHUB_OUTPUT).  Refuses to
touch a submodule with local changes, which build.sh makes while building
(run it on a clean checkout, as CI does).

Usage: python3 -m scripts.update_deps --out DIR
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from scripts.common import (
    ROOT,
    Submodule,
    branch_at,
    git,
    remote_tags,
    run,
    submodules,
    version,
)


@dataclass(frozen=True)
class Update:
    name: str
    url: str
    old: str  # tag, or a short commit if the old pin wasn't a release
    new: str


def glaze_majors(root: Path = ROOT) -> range | None:
    """The glaze major versions Hyprland accepts, as range(lo, hi)."""
    cmake = (root / "src/Hyprland/CMakeLists.txt").read_text()
    m = re.search(r"find_package\(glaze\s+(\d+)\.\.\.<(\d+)", cmake)
    return range(int(m.group(1)), int(m.group(2))) if m else None


def newest_release(name: str, tags: dict[str, str], root: Path = ROOT) -> str | None:
    """The newest release tag a submodule may move to."""
    candidates = list(tags)
    if name == "glaze" and (majors := glaze_majors(root)):
        candidates = [t for t in candidates if version(t)[0] in majors]
    return max(candidates, key=version, default=None)


def release_link(url: str, tag: str) -> str:
    """A link to a tag's release page: GitHub's, or GitLab's for anything else."""
    base = url.removesuffix(".git")
    if urlsplit(url).hostname == "github.com":
        return f"{base}/releases/tag/{tag}"
    return f"{base}/-/tags/{tag}"


def update_submodule(mod: Submodule, root: Path = ROOT) -> Update | None:
    """Check out the newest release of one submodule, if it isn't already."""
    sub = root / mod.path
    if git("status", "--porcelain", cwd=sub):
        sys.exit(f"{mod.path} has local changes; run this on a clean checkout")
    tags = remote_tags(mod.url, releases_only=True)
    latest = newest_release(mod.name, tags, root)
    if latest is None:
        print(f"{mod.name}: no release tags found, skipping", file=sys.stderr)
        return None
    head = git("rev-parse", "HEAD", cwd=sub)
    current = next((t for t, sha in tags.items() if sha == head), head[:12])
    if tags[latest] == head:
        print(f"{mod.name}: {current} is the latest release", file=sys.stderr)
        return None
    print(f"{mod.name}: {current} -> {latest}", file=sys.stderr)
    git("fetch", "-q", "--depth", "1", "origin", f"refs/tags/{latest}:refs/tags/{latest}", cwd=sub)
    git("-c", "advice.detachedHead=false", "checkout", "-q", latest, cwd=sub)
    if mod.name == "Hyprland":
        git("submodule", "update", "-q", "--init", "--recursive", "--depth", "1", cwd=sub)
        record_release_branch(mod, tags[latest], root)
    return Update(mod.name, mod.url, current, latest)


def record_release_branch(mod: Submodule, commit: str, root: Path = ROOT) -> None:
    """Record upstream's release branch for the pinned commit in .gitmodules.

    build.sh checks the commit out on a local branch of that name, so
    `Hyprland --version` reports it.  Without exactly one upstream branch at
    the commit (see branch_at), the setting is removed and the branch stays
    empty.
    """
    branch = branch_at(mod.url, commit)
    if branch:
        git("config", "-f", ".gitmodules", f"{mod.section}.branch", branch, cwd=root)
        print(f"{mod.name}: release branch {branch}", file=sys.stderr)
    else:
        git(
            "config",
            "-f",
            ".gitmodules",
            "--unset-all",
            f"{mod.section}.branch",
            cwd=root,
            check=False,
        )
        print(f"{mod.name}: no unique release branch at {commit[:12]}", file=sys.stderr)


def write_pr_files(updates: list[Update], next_tag: str, out: Path) -> None:
    """Write title.txt, summary.md and commit.txt for the update PR."""
    title = "Update " + ", ".join(f"{u.name} to {u.new}" for u in updates)
    rows = "\n".join(
        f"| {u.name} | {u.old} | [{u.new}]({release_link(u.url, u.new)}) |" for u in updates
    )
    summary = f"""Upstream released new versions:

| Project | From | To |
|---|---|---|
{rows}

CI builds and tests this PR; its `.deb` (an artifact of the CI run) can be
installed for testing with `scripts/install-ci-build.sh <PR number>`.

After merging, release it by pushing the tag **`{next_tag}`**:

    git fetch origin && git tag {next_tag} origin/main && git push origin {next_tag}

That publishes a pre-release with the `.deb`; promote it to a full release
once it works for you.
"""
    commit = title + "\n\n" + "\n".join(f"- {u.name}: {u.old} -> {u.new}" for u in updates)
    (out / "title.txt").write_text(title + "\n")
    (out / "summary.md").write_text(summary)
    (out / "commit.txt").write_text(commit + "\n")


def main(argv: list[str] | None = None, root: Path = ROOT) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--out", type=Path, required=True, help="directory for title.txt, summary.md and commit.txt"
    )
    args = parser.parse_args(argv)

    updates = [u for mod in submodules(root) if (u := update_submodule(mod, root))]
    args.out.mkdir(parents=True, exist_ok=True)
    if not updates:
        print("changed=false")
        return
    next_tag = run(root / "scripts/next-release.sh", cwd=root).split()[0]
    write_pr_files(updates, next_tag, args.out)
    print("changed=true")


if __name__ == "__main__":
    main()
