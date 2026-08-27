#!/usr/bin/env python3

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

"""Verify a vendored deployment bundle against its pinned render inputs."""

from __future__ import annotations

import argparse
import filecmp
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Final, NoReturn, Sequence

from render_core import RenderError, render_bundle
from validate_profile import ProfileError, load_profile, read_bounded_regular_file


MAX_BUNDLE_FILES: Final = 256
MAX_BUNDLE_FILE_BYTES: Final = 2 * 1024 * 1024
DIRECTORY_MODE: Final = 0o755


class ConformanceError(ValueError):
    """Raised when a vendored bundle differs from its deterministic render."""


def fail(message: str) -> NoReturn:
    """Raise one deterministic conformance failure."""

    raise ConformanceError(message)


def collect_tree(root: Path) -> tuple[dict[str, int], dict[str, int]]:
    """Collect file and directory modes without following filesystem aliases."""

    try:
        root_metadata = root.lstat()
    except OSError as error:
        fail(f"cannot inspect bundle root {root}: {error}")
    if not stat.S_ISDIR(root_metadata.st_mode) or root.is_symlink():
        fail(f"bundle root must be a real directory: {root}")
    root_mode = stat.S_IMODE(root_metadata.st_mode)
    if root_mode != DIRECTORY_MODE:
        fail(f"bundle root mode is {root_mode:04o}, expected {DIRECTORY_MODE:04o}")

    files: dict[str, int] = {}
    directories: dict[str, int] = {".": root_mode}
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = tuple(os.scandir(directory))
        except OSError as error:
            fail(f"cannot enumerate bundle directory {directory}: {error}")
        for entry in sorted(entries, key=lambda item: item.name):
            relative = Path(entry.path).relative_to(root).as_posix()
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as error:
                fail(f"cannot inspect bundle entry {relative}: {error}")
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISLNK(metadata.st_mode):
                fail(f"bundle entry must not be a symbolic link: {relative}")
            if stat.S_ISDIR(metadata.st_mode):
                if mode != DIRECTORY_MODE:
                    fail(
                        f"bundle directory mode is {mode:04o}, expected "
                        f"{DIRECTORY_MODE:04o}: {relative}"
                    )
                directories[relative] = mode
                pending.append(Path(entry.path))
                continue
            if not stat.S_ISREG(metadata.st_mode):
                fail(f"bundle entry must be a regular file: {relative}")
            if metadata.st_nlink != 1:
                fail(f"bundle file must have exactly one hard link: {relative}")
            files[relative] = mode
            if len(files) > MAX_BUNDLE_FILES:
                fail(f"bundle exceeds the {MAX_BUNDLE_FILES}-file limit")
    return files, directories


def compare_files(expected: Path, actual: Path, relative_paths: Sequence[str]) -> None:
    """Compare bounded file bytes for an already mode-matched tree."""

    for relative in relative_paths:
        expected_path = expected / relative
        actual_path = actual / relative
        expected_bytes = read_bounded_regular_file(
            expected_path, MAX_BUNDLE_FILE_BYTES, "expected bundle file"
        )
        actual_bytes = read_bounded_regular_file(
            actual_path, MAX_BUNDLE_FILE_BYTES, "vendored bundle file"
        )
        if expected_bytes != actual_bytes:
            fail(f"vendored bundle content differs: {relative}")
        if not filecmp.cmp(expected_path, actual_path, shallow=False):
            fail(f"vendored bundle comparison changed while reading: {relative}")


def verify_bundle(
    profile_path: Path,
    bundle_path: Path,
    source_revision: str,
) -> None:
    """Render expected output and compare one vendored tree exactly."""

    profile = load_profile(profile_path)
    actual_files, actual_directories = collect_tree(bundle_path)
    with tempfile.TemporaryDirectory(prefix="application-bundle-conformance-") as directory:
        expected = Path(directory) / "expected"
        render_bundle(profile, expected, source_revision)
        expected_files, expected_directories = collect_tree(expected)
        if actual_directories != expected_directories:
            missing = sorted(expected_directories.keys() - actual_directories.keys())
            extra = sorted(actual_directories.keys() - expected_directories.keys())
            detail = []
            if missing:
                detail.append(f"missing directories: {', '.join(missing)}")
            if extra:
                detail.append(f"extra directories: {', '.join(extra)}")
            fail("vendored bundle directory tree differs; " + "; ".join(detail))
        if actual_files.keys() != expected_files.keys():
            missing = sorted(expected_files.keys() - actual_files.keys())
            extra = sorted(actual_files.keys() - expected_files.keys())
            detail = []
            if missing:
                detail.append(f"missing files: {', '.join(missing)}")
            if extra:
                detail.append(f"extra files: {', '.join(extra)}")
            fail("vendored bundle file tree differs; " + "; ".join(detail))
        for relative, expected_mode in expected_files.items():
            actual_mode = actual_files[relative]
            if actual_mode != expected_mode:
                fail(
                    f"vendored bundle mode is {actual_mode:04o}, expected "
                    f"{expected_mode:04o}: {relative}"
                )
        compare_files(expected, bundle_path, tuple(sorted(expected_files)))


def build_parser() -> argparse.ArgumentParser:
    """Create the conformance command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--source-revision", required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Run bundle conformance verification."""

    options = build_parser().parse_args(arguments)
    try:
        verify_bundle(options.profile, options.bundle, options.source_revision)
    except (ConformanceError, OSError, ProfileError, RenderError) as error:
        print(f"verify-bundle: ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Vendored deployment bundle conforms: {options.bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
