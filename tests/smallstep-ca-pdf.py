#!/usr/bin/env python3

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

"""Focused parser and safety tests for the Smallstep CA PDF renderer."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Final
from unittest import mock

sys.dont_write_bytecode = True


REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
RENDERER_PATH: Final = REPOSITORY_ROOT / "smallstep-ca" / "build-security-standard-pdf.py"


def load_renderer() -> ModuleType:
    """Load the renderer without running its command-line entry point."""

    spec = importlib.util.spec_from_file_location("smallstep_ca_pdf", RENDERER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load renderer: {RENDERER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RENDERER: Final = load_renderer()
SYNTHETIC_IDENTIFIER: Final = "fixture-client.invalid"
SYNTHETIC_PATTERN: Final = r"fixture-client\.invalid"


def minimal_standard(extra_text: str = "") -> str:
    """Return the smallest canonical source accepted by the renderer."""

    sections = ["# Minimal CA standard"]
    sections.extend(f"## {heading}\n\nValidated content." for heading in RENDERER.REQUIRED_HEADINGS)
    if extra_text:
        sections.append(extra_text)
    return "\n\n".join(sections) + "\n"


class MarkdownParserTests(unittest.TestCase):
    """Exercise the constrained Markdown parser's stable behavior."""

    def test_parser_handles_lists_tables_code_and_markup(self) -> None:
        """Supported block types remain distinct and retain their content."""

        source = """# Title

Paragraph with `code` and **emphasis**.

- one
- two

1. first
2. second

| Name | Value |
| --- | --- |
| alpha | beta |

```sh
command --flag
```
"""
        blocks = RENDERER.parse_markdown(source)
        self.assertEqual(
            [block.kind for block in blocks],
            ["heading", "paragraph", "bullet", "bullet", "numbered", "numbered", "table", "code"],
        )
        self.assertEqual(blocks[-2].rows, (("Name", "Value"), ("alpha", "beta")))
        self.assertEqual(blocks[-1].text, "command --flag")

    def test_unterminated_code_fence_is_rejected(self) -> None:
        """A malformed fenced block cannot silently consume later content."""

        with self.assertRaisesRegex(ValueError, "unterminated fenced code block"):
            RENDERER.parse_markdown("# Title\n\n```sh\ncommand\n")

    def test_inconsistent_table_shape_is_rejected(self) -> None:
        """Rows cannot drift from the declared table width."""

        with self.assertRaisesRegex(ValueError, "inconsistent columns"):
            RENDERER.parse_markdown(
                "| Name | Value |\n| --- | --- |\n| only-one |\n"
            )

    def test_code_wrapping_bounds_long_lines(self) -> None:
        """Long command lines are wrapped to a predictable printable width."""

        wrapped = RENDERER.wrap_code("command " + "x" * 120, width=32)
        self.assertGreater(len(wrapped.splitlines()), 1)
        self.assertTrue(all(len(line) <= 32 for line in wrapped.splitlines()))


class CanonicalBoundaryTests(unittest.TestCase):
    """Verify canonical-source and output-path safety gates."""

    def setUp(self) -> None:
        """Use a synthetic rule independently of the operator's environment."""

        environment = mock.patch.dict(
            os.environ, {"CONFIG_PRIVATE_IDENTIFIER_PATTERN": SYNTHETIC_PATTERN}
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_required_heading_is_enforced(self) -> None:
        """Removing a critical section makes the source invalid."""

        blocks = RENDERER.parse_markdown("# Minimal\n\n## Install\n\nContent.\n")
        with self.assertRaisesRegex(ValueError, "missing headings"):
            RENDERER.validate_blocks(blocks)

    def test_private_identifier_is_rejected(self) -> None:
        """A configured synthetic identifier is rejected without disclosing it."""

        blocks = RENDERER.parse_markdown(minimal_standard(SYNTHETIC_IDENTIFIER))
        with self.assertRaises(ValueError) as raised:
            RENDERER.validate_blocks(blocks)
        self.assertNotIn(SYNTHETIC_IDENTIFIER, str(raised.exception))
        self.assertNotIn(SYNTHETIC_PATTERN, str(raised.exception))

    def test_private_identifier_in_table_is_rejected(self) -> None:
        """Table cells pass through the same configured privacy boundary."""

        table = f"| Name | Value |\n| --- | --- |\n| Endpoint | {SYNTHETIC_IDENTIFIER} |\n"
        blocks = RENDERER.parse_markdown(minimal_standard(table))
        with self.assertRaises(ValueError) as raised:
            RENDERER.validate_blocks(blocks)
        self.assertNotIn(SYNTHETIC_IDENTIFIER, str(raised.exception))

    def test_check_mode_requires_distinct_output(self) -> None:
        """The renderer cannot replace its own canonical Markdown source."""

        with tempfile.TemporaryDirectory(prefix="smallstep-ca-pdf-tests.") as temporary:
            source_path = Path(temporary) / "standard.md"
            source_path.write_text(minimal_standard(), encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                RENDERER.main(
                    ["--check", "--source", str(source_path), "--output", str(source_path)]
                )
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("output must differ", stderr.getvalue())

    def test_check_mode_validates_without_pdf_dependency(self) -> None:
        """Canonical validation stays usable where ReportLab is not installed."""

        with tempfile.TemporaryDirectory(prefix="smallstep-ca-pdf-tests.") as temporary:
            source_path = Path(temporary) / "standard.md"
            output_path = Path(temporary) / "standard.pdf"
            source_path.write_text(minimal_standard(), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = RENDERER.main(
                    ["--check", "--source", str(source_path), "--output", str(output_path)]
                )
        self.assertEqual(status, 0)
        self.assertIn("Validated canonical Smallstep CA security standard", stdout.getvalue())
        self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
