#!/usr/bin/env python3

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

"""Audit tracked source, staged content, and existing PDFs using private patterns."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "lib"))
from private_identifiers import (  # noqa: E402
    COMMAND_TIMEOUT_SECONDS,
    load_private_pattern,
    matches_private_pattern,
    validate_text,
)


def run_checked(arguments: Sequence[str], *, data: bytes | None = None) -> bytes:
    """Capture diagnostics rather than echoing filenames, configuration, or data."""

    try:
        result = subprocess.run(
            arguments, input=data, capture_output=True, check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("client-neutrality audit could not execute a required reader") from error
    if result.returncode:
        raise ValueError("client-neutrality audit reader failed")
    return result.stdout


def content_text(data: bytes) -> str:
    """Extract PDF text and metadata without generating or modifying an artifact."""

    if data.startswith(b"%PDF-"):
        data = run_checked(["pdftotext", "-", "-"], data=data) + run_checked(
            ["pdfinfo", "-"], data=data
        )
    # Preserve byte-oriented matching for non-UTF-8 assets too. PDF compression
    # is handled above; other opaque binary formats require separate review.
    return data.decode("utf-8", errors="replace")


def scan_repository(root: Path, *, staged: bool, paths: Sequence[str]) -> int:
    """Check every selected Git-tracked path and its chosen content snapshot."""

    pattern = load_private_pattern(root)
    if not pattern:
        print("Client-neutrality pattern is not configured; private-name scan skipped.")
        return 0
    # Validate even an empty repository's configured expression.
    matches_private_pattern("", pattern)
    listing = run_checked(["git", "-C", str(root), "ls-files", "-z", "--", *paths])
    count = 0
    for encoded in listing.split(b"\x00"):
        if not encoded:
            continue
        relative = encoded.decode("utf-8", errors="strict")
        if matches_private_pattern(relative, pattern):
            raise ValueError("tracked path contains client identifiers (path redacted)")
        if staged:
            data = run_checked(["git", "-C", str(root), "show", f":{relative}"])
        else:
            source = root / relative
            if source.is_symlink():
                data = str(source.readlink()).encode()
            elif not source.exists():
                continue  # An unstaged deletion has no working-tree content.
            else:
                data = source.read_bytes()
        try:
            validate_text(content_text(data), pattern=pattern)
        except ValueError as error:
            raise ValueError(f"{relative}: {error}") from error
        count += 1
    snapshot = "staged" if staged else "working-tree"
    print(f"Validated {count} tracked {snapshot} files with the private audit pattern.")
    return count


def main() -> int:
    """Expose optional component scope and index checks for local publication gates."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="read index content")
    parser.add_argument("paths", nargs="*", help="optional repository-relative Git pathspecs")
    arguments = parser.parse_args()
    try:
        scan_repository(REPOSITORY_ROOT, staged=arguments.staged, paths=arguments.paths)
    except (ValueError, OSError, UnicodeError) as error:
        # OSError paths and UnicodeError payloads can carry private bytes.
        message = str(error) if isinstance(error, ValueError) and not isinstance(error, UnicodeError) else "client-neutrality audit could not read tracked content"
        print(f"client-neutrality: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
