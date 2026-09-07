#!/usr/bin/env python3

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

"""Exercise private configuration, redaction, and staged audit boundaries."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from typing import Final
from unittest import mock

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "lib"))
from private_identifiers import (  # noqa: E402
    MAX_PATTERN_BYTES,
    PATTERN_VARIABLE,
    load_private_pattern,
    matches_private_pattern,
    parse_pattern_value,
    validate_text,
)

SPEC: Final = importlib.util.spec_from_file_location(
    "client_neutrality", REPOSITORY_ROOT / "tests" / "client-neutrality.py"
)
assert SPEC is not None and SPEC.loader is not None
SCANNER: Final = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCANNER)
SYNTHETIC_PATTERN: Final = r"fixture-client\.invalid|fixture-host[[:digit:]]+"


class PrivatePatternTests(unittest.TestCase):
    """Private pattern configuration never executes code or reveals its values."""

    def test_environment_precedes_file_including_explicit_empty(self) -> None:
        """An explicit environment setting can replace or disable local policy."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(f"{PATTERN_VARIABLE}='from-file'\n")
            for value in ("from-environment", ""):
                with self.subTest(value=value), mock.patch.dict(os.environ, {PATTERN_VARIABLE: value}):
                    self.assertEqual(load_private_pattern(root), value)

    def test_dotenv_is_literal_data_and_preserves_regex(self) -> None:
        """Quoted ERE escapes survive while shell-looking content is inert."""

        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            marker = root / "executed"
            (root / ".env").write_text(
                f"IGNORED=$(touch {marker})\nexport {PATTERN_VARIABLE}='{SYNTHETIC_PATTERN}' # local\n"
            )
            self.assertEqual(load_private_pattern(root), SYNTHETIC_PATTERN)
            self.assertFalse(marker.exists())
            self.assertEqual(parse_pattern_value(r'"fixture\.invalid" # comment'), r"fixture\.invalid")
            self.assertEqual(parse_pattern_value(r"fixture\.invalid # comment"), r"fixture\.invalid")

    def test_invalid_configuration_fails_without_echoing_values(self) -> None:
        """Malformed, ambiguous, and nonregular configuration cannot silently skip."""

        bad_values = (
            f"{PATTERN_VARIABLE}='fixture-client.invalid\n",
            f"{PATTERN_VARIABLE}='fixture-client.invalid' extra\n",
            f"{PATTERN_VARIABLE}=one\n{PATTERN_VARIABLE}=fixture-client.invalid\n",
            "touch fixture-client.invalid\n",
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            configuration = root / ".env"
            for value in bad_values:
                with self.subTest(value=value):
                    configuration.write_text(value)
                    with self.assertRaises(ValueError) as raised:
                        load_private_pattern(root)
                    self.assertNotIn("fixture-client.invalid", str(raised.exception))
            configuration.unlink()
            configuration.symlink_to(root / "missing")
            with self.assertRaisesRegex(ValueError, "regular file"):
                load_private_pattern(root)

    def test_posix_matching_and_redaction(self) -> None:
        """Case-insensitive POSIX classes work, and neither matches nor patterns leak."""

        for value in ("FIXTURE-CLIENT.INVALID", "fixture-host27"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "client identifiers") as raised:
                validate_text(value, pattern=SYNTHETIC_PATTERN)
            self.assertNotIn(value, str(raised.exception))
            self.assertNotIn(SYNTHETIC_PATTERN, str(raised.exception))
        self.assertFalse(matches_private_pattern("fixture-clientXinvalid", SYNTHETIC_PATTERN))
        self.assertFalse(matches_private_pattern("anything", ""))
        for pattern in ("[", "one\ntwo", "\x00", "x" * (MAX_PATTERN_BYTES + 1)):
            with self.subTest(pattern=pattern[:8]), self.assertRaises(ValueError):
                matches_private_pattern("", pattern)

    def test_pattern_is_not_a_process_argument(self) -> None:
        """The grep process receives the expression through an inherited pipe."""

        with mock.patch("private_identifiers.subprocess.run", wraps=subprocess.run) as command:
            validate_text("clean content", pattern=SYNTHETIC_PATTERN)
        self.assertNotIn(SYNTHETIC_PATTERN, " ".join(command.call_args.args[0]))

    def test_pattern_limit_is_a_fixed_byte_count(self) -> None:
        """The same pattern is accepted on every platform up to the documented bound."""

        # PIPE_BUF is 512 bytes on macOS and irrelevant to pipe capacity; the
        # matcher must not consult it, or a pattern would pass in CI and fail
        # on a developer machine.
        with mock.patch("private_identifiers.os.fpathconf", side_effect=AssertionError("consulted PIPE_BUF")):
            self.assertFalse(matches_private_pattern("clean", "x" * MAX_PATTERN_BYTES))
            self.assertTrue(matches_private_pattern("x" * 600, "x" * 600))
            with self.assertRaisesRegex(ValueError, f"{MAX_PATTERN_BYTES} UTF-8 bytes"):
                matches_private_pattern("clean", "x" * (MAX_PATTERN_BYTES + 1))
            # The bound counts encoded bytes, not characters.
            two_byte_character = "é"
            self.assertFalse(matches_private_pattern("clean", two_byte_character * (MAX_PATTERN_BYTES // 2)))
            with self.assertRaisesRegex(ValueError, f"{MAX_PATTERN_BYTES} UTF-8 bytes"):
                matches_private_pattern("clean", two_byte_character * (MAX_PATTERN_BYTES // 2 + 1))

    def test_pattern_write_never_blocks_on_the_unread_pipe(self) -> None:
        """A pipe that cannot take the whole expression fails instead of waiting."""

        with self.subTest(outcome="refused"), mock.patch(
            "private_identifiers.os.write", side_effect=BlockingIOError()
        ), self.assertRaisesRegex(ValueError, "without blocking"):
            matches_private_pattern("clean", SYNTHETIC_PATTERN)
        with self.subTest(outcome="short"), mock.patch(
            "private_identifiers.os.write", return_value=7
        ), self.assertRaisesRegex(ValueError, "without blocking"):
            matches_private_pattern("clean", SYNTHETIC_PATTERN)

    def test_dotenv_byte_order_mark_and_comment_quoting(self) -> None:
        """A BOM does not corrupt the first assignment; quoting protects ' #' in a pattern."""

        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            (root / ".env").write_text(f"{PATTERN_VARIABLE}='{SYNTHETIC_PATTERN}'\n", encoding="utf-8-sig")
            self.assertEqual(load_private_pattern(root), SYNTHETIC_PATTERN)
        # Unquoted, whitespace followed by '#' starts the comment, as documented;
        # quoting keeps the bracket expression intact.
        self.assertEqual(parse_pattern_value("[ #]x"), "[")
        self.assertEqual(parse_pattern_value("'[ #]x'"), "[ #]x")
        self.assertTrue(matches_private_pattern("#x", "[ #]x"))

    def test_reader_errors_do_not_expose_exception_chains(self) -> None:
        """Public generator tracebacks must hide private filesystem diagnostics."""

        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            (root / ".env").write_text(f"{PATTERN_VARIABLE}=safe\n")
            for operation in ("lstat", "read_text"):
                with self.subTest(operation=operation), mock.patch.object(
                    Path, operation, side_effect=PermissionError("fixture-client.invalid")
                ):
                    try:
                        load_private_pattern(root)
                    except ValueError:
                        self.assertNotIn("fixture-client.invalid", traceback.format_exc())
                    else:
                        self.fail("a private configuration reader failure must be rejected")

    def test_matcher_errors_do_not_expose_exception_chains(self) -> None:
        """A failed matcher cannot reveal captured command or environment details."""

        with mock.patch(
            "private_identifiers.subprocess.run", side_effect=OSError("fixture-client.invalid")
        ):
            try:
                matches_private_pattern("public", SYNTHETIC_PATTERN)
            except ValueError:
                self.assertNotIn("fixture-client.invalid", traceback.format_exc())
            else:
                self.fail("a matcher failure must be rejected")


class RepositoryAuditTests(unittest.TestCase):
    """Exercise a real temporary index independently of working-tree content."""

    def test_index_content_cannot_hide_behind_a_clean_working_copy(self) -> None:
        """An unstaged cleanup must not hide the content that would be committed."""

        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {PATTERN_VARIABLE: SYNTHETIC_PATTERN}):
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            source = root / "sample.txt"
            source.write_text("fixture-client.invalid\n")
            subprocess.run(["git", "-C", str(root), "add", "sample.txt"], check=True)
            source.write_text("public.example.invalid\n")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(SCANNER.scan_repository(root, staged=False, paths=[]), 1)
            with self.assertRaisesRegex(ValueError, "sample.txt:.*client identifiers") as raised:
                SCANNER.scan_repository(root, staged=True, paths=[])
            self.assertNotIn("fixture-client.invalid", str(raised.exception))

    def test_private_filenames_are_redacted(self) -> None:
        """A matching tracked filename is rejected before being printed."""

        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {PATTERN_VARIABLE: SYNTHETIC_PATTERN}):
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / "fixture-client.invalid.txt").write_text("clean\n")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            with self.assertRaisesRegex(ValueError, "path redacted") as raised:
                SCANNER.scan_repository(root, staged=False, paths=[])
            self.assertNotIn("fixture-client.invalid", str(raised.exception))

    def test_pdf_reader_failure_does_not_silently_pass(self) -> None:
        """PDF content requires successful text and metadata readers."""

        with mock.patch.object(SCANNER, "run_checked", side_effect=[b"text", b"metadata"]):
            self.assertEqual(SCANNER.content_text(b"%PDF-fixture"), "textmetadata")
        with mock.patch.object(SCANNER, "run_checked", side_effect=ValueError("reader failed")):
            with self.assertRaisesRegex(ValueError, "reader failed"):
                SCANNER.content_text(b"%PDF-fixture")


if __name__ == "__main__":
    unittest.main()
