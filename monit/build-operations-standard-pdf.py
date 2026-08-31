#!/usr/bin/env python3

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

"""Render the canonical Monit operations standard as a polished PDF."""

from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence

DOCUMENT_DATE: Final[str] = "August 31, 2026"
DOCUMENT_VERSION: Final[str] = "1.0"
DEFAULT_OUTPUT_RELATIVE: Final[Path] = Path(
    "output/pdf/monit-operations-standard.pdf"
)
REQUIRED_HEADINGS: Final[tuple[str, ...]] = (
    "Supported platform and version policy",
    "Security and ownership model",
    "Installed layout",
    "Install",
    "Render and review setup",
    "Apply and adopt",
    "Deployment fragment contract",
    "Log rotation and SELinux",
    "Verify and collect acceptance evidence",
    "Rollback",
)
PAGE_BREAK_HEADINGS: Final[frozenset[str]] = frozenset(
    {
        "Installed layout",
        "Render and review setup",
        "Deployment fragment contract",
        "Verify and collect acceptance evidence",
        "Rollback",
    }
)
ASCII_REPLACEMENTS: Final[dict[str, str]] = {
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u00a0": " ",
}


@dataclass(frozen=True)
class MarkdownBlock:
    """One validated block consumed by the PDF renderer."""

    kind: str
    text: str = ""
    level: int = 0
    rows: tuple[tuple[str, ...], ...] = ()


def repository_root(script_path: Path) -> Path:
    """Return the repository root for a renderer inside ``monit``."""

    return script_path.resolve().parent.parent


def normalize_ascii(text: str) -> str:
    """Normalize punctuation for the built-in PDF fonts."""

    normalized = text
    for source, replacement in ASCII_REPLACEMENTS.items():
        normalized = normalized.replace(source, replacement)
    return normalized


def parse_table(lines: Sequence[str]) -> MarkdownBlock:
    """Parse a simple GitHub-flavored Markdown table."""

    rows: list[tuple[str, ...]] = []
    for line_number, line in enumerate(lines):
        cells = tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
        if line_number == 1 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    if len(rows) < 2:
        raise ValueError("Markdown table must contain a header and data")
    column_count = len(rows[0])
    if column_count < 2 or any(len(row) != column_count for row in rows):
        raise ValueError("Markdown table has inconsistent columns")
    return MarkdownBlock("table", rows=tuple(rows))


def parse_markdown(source_text: str) -> list[MarkdownBlock]:
    """Parse the constrained Markdown subset used by the canonical standard."""

    blocks: list[MarkdownBlock] = []
    paragraph_lines: list[str] = []
    code_lines: list[str] = []
    table_lines: list[str] = []
    in_code = False

    def flush_paragraph() -> None:
        if paragraph_lines:
            blocks.append(MarkdownBlock("paragraph", " ".join(paragraph_lines)))
            paragraph_lines.clear()

    def flush_table() -> None:
        if table_lines:
            blocks.append(parse_table(table_lines))
            table_lines.clear()

    for raw_line in source_text.splitlines():
        line = normalize_ascii(raw_line.rstrip())
        if line.startswith("```"):
            flush_paragraph()
            flush_table()
            if in_code:
                blocks.append(MarkdownBlock("code", "\n".join(code_lines)))
                code_lines.clear()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if line.startswith("|"):
            flush_paragraph()
            table_lines.append(line)
            continue
        flush_table()
        if not line:
            flush_paragraph()
            continue
        heading_match = re.fullmatch(r"(#{1,3})\s+(.+)", line)
        if heading_match:
            flush_paragraph()
            blocks.append(
                MarkdownBlock(
                    "heading",
                    heading_match.group(2),
                    level=len(heading_match.group(1)),
                )
            )
            continue
        bullet_match = re.fullmatch(r"-\s+(.+)", line)
        if bullet_match:
            flush_paragraph()
            blocks.append(MarkdownBlock("bullet", bullet_match.group(1)))
            continue
        numbered_match = re.fullmatch(r"\d+\.\s+(.+)", line)
        if numbered_match:
            flush_paragraph()
            blocks.append(MarkdownBlock("numbered", numbered_match.group(1)))
            continue
        if line.startswith("  ") and blocks and blocks[-1].kind in {"bullet", "numbered"}:
            previous = blocks[-1]
            blocks[-1] = MarkdownBlock(
                previous.kind,
                f"{previous.text} {line.strip()}",
                previous.level,
                previous.rows,
            )
            continue
        paragraph_lines.append(line.strip())

    flush_paragraph()
    flush_table()
    if in_code:
        raise ValueError("unterminated fenced code block")
    if not blocks:
        raise ValueError("canonical source is empty")
    return blocks


def validate_blocks(blocks: Sequence[MarkdownBlock]) -> None:
    """Require critical sections and enforce the client-neutral boundary."""

    headings = {block.text for block in blocks if block.kind == "heading"}
    missing = [heading for heading in REQUIRED_HEADINGS if heading not in headings]
    if missing:
        raise ValueError(f"canonical source is missing headings: {', '.join(missing)}")
    text_parts = [block.text for block in blocks]
    for block in blocks:
        text_parts.extend(cell for row in block.rows for cell in row)
    source_text = "\n".join(text_parts).lower()
    forbidden = (
        "omi" + "celo",
        "vul" + "can",
        "haven" + "side",
        "om" + "redis",
        "om" + "web",
    )
    present = [token for token in forbidden if token in source_text]
    if present:
        raise ValueError(f"public standard contains client identifiers: {present}")


def inline_markup(text: str) -> str:
    """Convert links, code, and bold spans to safe ReportLab markup."""

    normalized = normalize_ascii(text)
    token_pattern = re.compile(
        r"(\[[^\]]+\]\(https?://[^)]+\)|`[^`]+`|\*\*[^*]+\*)"
    )
    parts: list[str] = []
    position = 0
    for match in token_pattern.finditer(normalized):
        parts.append(html.escape(normalized[position : match.start()]))
        token = match.group(0)
        if token.startswith("["):
            link_match = re.fullmatch(r"\[([^\]]+)\]\((https?://[^)]+)\)", token)
            if link_match is None:
                raise ValueError(f"invalid link token: {token}")
            label, target = link_match.groups()
            parts.append(
                f'<link href="{html.escape(target, quote=True)}" color="#176B87">'
                f"{html.escape(label)}</link>"
            )
        elif token.startswith("`"):
            parts.append(
                '<font name="Courier" color="#153447">'
                f"{html.escape(token[1:-1])}</font>"
            )
        else:
            parts.append(f"<b>{html.escape(token[2:-2])}</b>")
        position = match.end()
    parts.append(html.escape(normalized[position:]))
    return "".join(parts)


def wrap_code(code: str, width: int = 88) -> str:
    """Wrap code deterministically so long command lines remain on the page."""

    wrapped: list[str] = []
    for line in code.splitlines() or [""]:
        if len(line) <= width:
            wrapped.append(line)
            continue
        indentation = " " * min(len(line) - len(line.lstrip()) + 2, 12)
        wrapped.extend(
            textwrap.wrap(
                line,
                width=width,
                subsequent_indent=indentation,
                break_long_words=True,
                break_on_hyphens=False,
                replace_whitespace=False,
                drop_whitespace=False,
            )
        )
    return "\n".join(wrapped)


def build_pdf(blocks: Sequence[MarkdownBlock], output_path: Path) -> None:
    """Build the final PDF atomically with ReportLab Platypus."""

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        KeepTogether,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        XPreformatted,
    )

    navy = colors.HexColor("#102E45")
    teal = colors.HexColor("#176B87")
    aqua = colors.HexColor("#64CCC5")
    pale = colors.HexColor("#EAF6F6")
    ink = colors.HexColor("#20313D")
    muted = colors.HexColor("#657681")
    light_rule = colors.HexColor("#CAD9DF")
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            "CoverTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=28,
            leading=33,
            textColor=colors.white,
            alignment=TA_LEFT,
            spaceAfter=14,
        )
    )
    styles.add(
        ParagraphStyle(
            "CoverSubtitle",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=12.5,
            leading=18,
            textColor=colors.HexColor("#D7EEF2"),
        )
    )
    styles.add(
        ParagraphStyle(
            "BodyCustom",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9.2,
            leading=13.2,
            textColor=ink,
            spaceAfter=7,
            allowWidows=0,
            allowOrphans=0,
        )
    )
    styles.add(
        ParagraphStyle(
            "HeadingOne",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=17,
            leading=21,
            textColor=navy,
            spaceBefore=12,
            spaceAfter=8,
            keepWithNext=False,
        )
    )
    styles.add(
        ParagraphStyle(
            "HeadingTwo",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=teal,
            spaceBefore=11,
            spaceAfter=5,
            keepWithNext=False,
        )
    )
    styles.add(
        ParagraphStyle(
            "ListCustom",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9.1,
            leading=12.8,
            textColor=ink,
            leftIndent=16,
            firstLineIndent=-8,
            spaceAfter=3,
        )
    )
    styles.add(
        ParagraphStyle(
            "CodeCustom",
            parent=styles["Code"],
            fontName="Courier",
            fontSize=6.5,
            leading=8.8,
            textColor=navy,
            backColor=pale,
            borderColor=light_rule,
            borderWidth=0.5,
            borderPadding=7,
            spaceBefore=4,
            spaceAfter=9,
        )
    )
    styles.add(
        ParagraphStyle(
            "TableHeader",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=9.5,
            textColor=colors.white,
        )
    )
    styles.add(
        ParagraphStyle(
            "TableCell",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.35,
            leading=9.5,
            textColor=ink,
        )
    )

    class SecurityDocument(BaseDocTemplate):
        """Document template with deterministic metadata and page furniture."""

        def __init__(self, filename: str) -> None:
            super().__init__(
                filename,
                pagesize=LETTER,
                leftMargin=0.72 * inch,
                rightMargin=0.72 * inch,
                topMargin=0.95 * inch,
                bottomMargin=0.68 * inch,
                title="Monit Operations Standard",
                author="Michael Welter",
                subject="Client-neutral virtual-machine monitoring baseline",
            )
            frame = Frame(
                self.leftMargin,
                self.bottomMargin,
                self.width,
                self.height,
                id="normal",
            )
            self.addPageTemplates(
                [PageTemplate(id="standard", frames=[frame], onPageEnd=self.draw_page)]
            )

        def draw_page(self, canvas: object, document: object) -> None:
            """Draw header, footer, and rules on every page after the cover."""

            page_number = getattr(document, "page", 1)
            canvas.saveState()
            canvas.setTitle("Monit Operations Standard")
            canvas.setAuthor("Michael Welter")
            if page_number > 1:
                canvas.setStrokeColor(light_rule)
                canvas.setLineWidth(0.55)
                canvas.line(0.72 * inch, 10.42 * inch, 7.78 * inch, 10.42 * inch)
                canvas.setFont("Helvetica-Bold", 7.5)
                canvas.setFillColor(teal)
                canvas.drawString(0.72 * inch, 10.53 * inch, "MONIT OPERATIONS STANDARD")
                canvas.setFont("Helvetica", 7.5)
                canvas.setFillColor(muted)
                footer = f"Version {DOCUMENT_VERSION}  |  {DOCUMENT_DATE}"
                canvas.drawString(0.72 * inch, 0.40 * inch, footer)
                page_text = f"{page_number}"
                canvas.drawString(
                    7.78 * inch - stringWidth(page_text, "Helvetica", 7.5),
                    0.40 * inch,
                    page_text,
                )
            canvas.restoreState()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    try:
        document = SecurityDocument(str(temporary_path))
        story: list[object] = []
        source_digest = hashlib.sha256(
            "\n".join(
                block.text + repr(block.rows) for block in blocks
            ).encode("utf-8")
        ).hexdigest()
        cover = Table(
            [
                [
                    Paragraph("Monit<br/>Operations Standard", styles["CoverTitle"]),
                ],
                [
                    Paragraph(
                        "SELinux-enforcing host monitoring, local-only control, "
                        "safe log rotation, and evidence-driven operations",
                        styles["CoverSubtitle"],
                    )
                ],
                [
                    Paragraph(
                        f"Version {DOCUMENT_VERSION}  |  {DOCUMENT_DATE}<br/>"
                        f"Canonical source digest: {source_digest[:20]}",
                        styles["CoverSubtitle"],
                    )
                ],
            ],
            colWidths=[7.06 * inch],
            rowHeights=[2.0 * inch, 1.25 * inch, 1.15 * inch],
        )
        cover.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), navy),
                    ("BOX", (0, 0), (-1, -1), 1.2, teal),
                    ("LINEBELOW", (0, 0), (-1, 0), 2.0, aqua),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 28),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 28),
                ]
            )
        )
        story.extend([Spacer(1, 1.0 * inch), cover, PageBreak()])

        numbered_index = 0
        first_source_heading = True
        for block in blocks:
            if block.kind == "heading":
                if block.level == 1:
                    if first_source_heading:
                        first_source_heading = False
                        continue
                    style = styles["HeadingOne"]
                else:
                    style = styles["HeadingTwo"]
                if (
                    block.text in PAGE_BREAK_HEADINGS
                    and story
                    and not isinstance(story[-1], PageBreak)
                ):
                    story.append(PageBreak())
                story.append(Paragraph(inline_markup(block.text), style))
                numbered_index = 0
            elif block.kind == "paragraph":
                story.append(
                    KeepTogether(
                        [Paragraph(inline_markup(block.text), styles["BodyCustom"])]
                    )
                )
                numbered_index = 0
            elif block.kind == "bullet":
                story.append(
                    KeepTogether(
                        [
                            Paragraph(
                                f"<b>•</b>&nbsp;&nbsp;{inline_markup(block.text)}",
                                styles["ListCustom"],
                            )
                        ]
                    )
                )
                numbered_index = 0
            elif block.kind == "numbered":
                numbered_index += 1
                story.append(
                    KeepTogether(
                        [
                            Paragraph(
                                f"<b>{numbered_index}.</b>&nbsp;&nbsp;{inline_markup(block.text)}",
                                styles["ListCustom"],
                            )
                        ]
                    )
                )
            elif block.kind == "code":
                story.append(
                    XPreformatted(
                        html.escape(wrap_code(block.text)), styles["CodeCustom"]
                    )
                )
                numbered_index = 0
            elif block.kind == "table":
                table_data: list[list[object]] = []
                for row_index, row in enumerate(block.rows):
                    cell_style = styles["TableHeader"] if row_index == 0 else styles["TableCell"]
                    table_data.append(
                        [Paragraph(inline_markup(cell), cell_style) for cell in row]
                    )
                if len(block.rows[0]) == 3:
                    column_widths = [2.75 * inch, 1.55 * inch, 2.76 * inch]
                else:
                    column_width = document.width / len(block.rows[0])
                    column_widths = [column_width] * len(block.rows[0])
                table = Table(
                    table_data,
                    colWidths=column_widths,
                    repeatRows=1,
                    hAlign="LEFT",
                )
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), teal),
                            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, pale]),
                            ("GRID", (0, 0), (-1, -1), 0.45, light_rule),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 5),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ]
                    )
                )
                story.extend([table, Spacer(1, 8)])
                numbered_index = 0
            else:
                raise ValueError(f"unsupported Markdown block: {block.kind}")

        document.build(story)
        if temporary_path.stat().st_size < 10_000:
            raise RuntimeError("rendered PDF is unexpectedly small")
        os.replace(temporary_path, output_path)
        output_path.chmod(0o644)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def build_argument_parser(default_source: Path, default_output: Path) -> argparse.ArgumentParser:
    """Create the command-line parser with deterministic defaults."""

    parser = argparse.ArgumentParser(
        description="Render the canonical Monit operations standard as PDF."
    )
    parser.add_argument("--source", type=Path, default=default_source)
    parser.add_argument("--output", type=Path, default=default_output)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate canonical content without importing PDF dependencies",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the canonical source and optionally render its PDF handoff."""

    script_path = Path(__file__)
    root = repository_root(script_path)
    parser = build_argument_parser(
        script_path.resolve().parent / "README.md",
        root / DEFAULT_OUTPUT_RELATIVE,
    )
    arguments = parser.parse_args(argv)
    source_path = arguments.source.resolve()
    output_path = arguments.output.resolve()
    if not source_path.is_file() or source_path.is_symlink():
        parser.error(f"source must be a regular, non-symbolic-link file: {source_path}")
    if output_path == source_path:
        parser.error("output must differ from the canonical source")
    blocks = parse_markdown(source_path.read_text(encoding="utf-8"))
    validate_blocks(blocks)
    if arguments.check:
        print(f"Validated canonical Monit operations standard: {source_path}")
        return 0
    build_pdf(blocks, output_path)
    print(f"Rendered Monit operations standard PDF: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
