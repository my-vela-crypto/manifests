#!/usr/bin/env python3
"""Apply the versioned EncFS source patches and app overlays without clobbering edits."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(repository, *arguments):
    return subprocess.run(["git", "-C", str(repository), *arguments],
                          capture_output=True, text=True)


def main():
    manifest = Path(__file__).resolve().parents[1]
    default_root = manifest.parent.parent if manifest.parent.name == ".repo" else Path.cwd()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=default_root)
    args = parser.parse_args()
    root = args.source_root.resolve()
    metadata = json.loads((manifest / "encfs-sources.json").read_text())
    patches = []
    copies = []

    # Finish every conflict check before changing any source file.
    for project in ("nuttx", "vendor/openvela"):
        repository = root / project
        if run(repository, "rev-parse", "--git-dir").returncode:
            raise RuntimeError(f"Not a checked-out repo project: {repository}")
        patch = manifest / metadata["patches"][project]["file"]
        if digest(patch) != metadata["patches"][project]["sha256"]:
            raise RuntimeError(f"Patch checksum mismatch: {patch}")
        reverse = run(repository, "apply", "--reverse", "--check", str(patch))
        if reverse.returncode == 0:
            print(f"Already applied: {project}")
            continue
        check = run(repository, "apply", "--check", str(patch))
        if check.returncode:
            raise RuntimeError(f"Patch conflicts in {project}; existing edits were preserved.\n"
                               f"Expected base: {metadata['patches'][project]['base']}\n"
                               f"{check.stderr.strip()}")
        patches.append((repository, patch))

    apps = (root / "apps").resolve()
    for relative, expected in sorted(metadata["overlays"].items()):
        source = manifest / "overlays" / "apps" / relative
        destination = apps / relative
        if digest(source) != expected:
            raise RuntimeError(f"Overlay checksum mismatch: {source}")
        if not destination.resolve().is_relative_to(apps):
            raise RuntimeError(f"Overlay destination escapes apps: {destination}")
        if destination.exists():
            if not destination.is_file() or digest(destination) != expected:
                raise RuntimeError(f"Overlay conflicts with existing file: {destination}")
        else:
            copies.append((source, destination))

    for repository, patch in patches:
        result = run(repository, "apply", str(patch))
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        print(f"Applied patch: {repository.relative_to(root)}")
    for source, destination in copies:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".encfs-new")
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    shortcut = root / "apply-encfs.sh"
    if not shortcut.exists() and not shortcut.is_symlink():
        shortcut.symlink_to(os.path.relpath(manifest / "tools/apply-encfs.sh", root))
    print(f"EncFS sources ready; {len(copies)} app files installed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"EncFS apply failed: {error}", file=sys.stderr)
        sys.exit(1)
