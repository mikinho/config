#!/usr/bin/env python3

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

"""Load private audit patterns as data and match without disclosing their values."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Final

PATTERN_VARIABLE: Final = "CONFIG_PRIVATE_IDENTIFIER_PATTERN"
REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
MAX_ENVIRONMENT_BYTES: Final = 64 * 1024
# The pattern is queued in an anonymous pipe before grep starts reading, so it
# must fit the smallest pipe either target platform allocates: one 4096-byte
# page on Linux once a user's pipe-page soft limit is reached, 16 KiB on macOS.
# One page less the terminating newline is that bound. It is a property of pipe
# capacity, not of PIPE_BUF, which only governs write atomicity and is 512
# bytes on macOS.
MAX_PATTERN_BYTES: Final = 4095
COMMAND_TIMEOUT_SECONDS: Final = 30
ASSIGNMENT: Final = re.compile(r"(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)")


def parse_pattern_value(value: str) -> str:
    """Decode a single dotenv value without executing or expanding shell syntax."""

    value = value.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        quote = value[0]
        end = value.find(quote, 1)
        if end < 0:
            raise ValueError("private audit configuration has an unterminated quote")
        suffix = value[end + 1 :].strip()
        if suffix and not suffix.startswith("#"):
            raise ValueError("private audit configuration has trailing value syntax")
        # Backslashes are regex data, including inside double quotes. No shell
        # substitutions, variable expansion, or escape evaluation takes place.
        return value[1:end]
    return re.split(r"[ \t]+#", value, maxsplit=1)[0].rstrip()


def load_private_pattern(repository_root: Path | None = None) -> str:
    """Prefer an explicit environment value, otherwise read the ignored .env."""

    if PATTERN_VARIABLE in os.environ:
        return os.environ[PATTERN_VARIABLE]
    environment_file = (repository_root or REPOSITORY_ROOT) / ".env"
    try:
        metadata = environment_file.lstat()
    except FileNotFoundError:
        return ""
    except OSError:
        raise ValueError("private audit .env metadata cannot be read") from None
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("private audit .env must be a regular file, not a symbolic link")
    if metadata.st_size > MAX_ENVIRONMENT_BYTES:
        raise ValueError("private audit .env exceeds the supported size")
    try:
        # Editors on the target desktops may prepend a byte-order mark; it is
        # not part of the first assignment's name.
        content = environment_file.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise ValueError("private audit .env cannot be read as UTF-8") from None
    pattern: str | None = None
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assignment = ASSIGNMENT.fullmatch(stripped)
        if assignment is None:
            raise ValueError("private audit .env must contain only single-line assignments")
        if assignment.group(1) != PATTERN_VARIABLE:
            continue
        if pattern is not None:
            raise ValueError("private audit .env contains a duplicate pattern assignment")
        pattern = parse_pattern_value(assignment.group(2))
    return pattern or ""


def matches_private_pattern(text: str, pattern: str) -> bool:
    """Use POSIX ERE matching while keeping pattern values out of argv and output."""

    if not pattern:
        return False
    if any(character in pattern for character in ("\x00", "\n", "\r")):
        raise ValueError("private audit pattern must occupy one line without NUL bytes")
    payload = pattern.encode("utf-8") + b"\n"
    if len(payload) > MAX_PATTERN_BYTES + 1:
        raise ValueError(f"private audit pattern exceeds {MAX_PATTERN_BYTES} UTF-8 bytes")
    # Feed the expression through an anonymous pipe, not a command argument or
    # named file. The target platforms provide /dev/fd and grep -E -i -f.
    try:
        read_fd, write_fd = os.pipe()
    except OSError:
        raise ValueError("private audit matcher could not open its input pipe") from None
    try:
        # No reader exists yet, so the write must never be allowed to block. A
        # non-blocking write of at most one page fits the smallest pipe either
        # platform allocates; a short or refused write is an error, not a wait.
        os.set_blocking(write_fd, False)
        try:
            written = os.write(write_fd, payload)
        except BlockingIOError:
            written = 0
        if written != len(payload):
            raise ValueError("private audit matcher could not queue the pattern without blocking")
        os.close(write_fd)
        write_fd = -1
        result = subprocess.run(
            ["grep", "-E", "-i", "-a", "-f", f"/dev/fd/{read_fd}"],
            input=text,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C"},
            pass_fds=(read_fd,),
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        raise ValueError("private audit matcher could not complete") from None
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
    if result.returncode not in (0, 1):
        raise ValueError("private audit pattern is invalid or matching failed")
    return result.returncode == 0


def validate_text(
    text: str,
    *,
    repository_root: Path | None = None,
    pattern: str | None = None,
) -> None:
    """Reject configured client identifiers without including matches in errors."""

    selected = load_private_pattern(repository_root) if pattern is None else pattern
    if matches_private_pattern(text, selected):
        raise ValueError("public content contains client identifiers (values redacted)")
