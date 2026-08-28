#!/usr/bin/env python3

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

"""Render the canonical Redis security standard as a polished PDF.

The repository Markdown file remains authoritative. This renderer intentionally
supports only the small Markdown subset used by ``redis/README.md`` so failures
are deterministic and unsupported constructs cannot silently disappear.
"""

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

DOCUMENT_DATE: Final[str] = "August 28, 2026"
DOCUMENT_VERSION: Final[str] = "1.1"
DEFAULT_OUTPUT_RELATIVE: Final[Path] = Path("output/pdf/redis-security-standard.pdf")
REQUIRED_HEADINGS: Final[tuple[str, ...]] = (
    "Platform and version policy",
    "Security baseline",
    "Data profiles",
    "Application ACL contract",
    "Local configurations",
    "Restricted-network configurations",
    "Verification and evidence",
    "Operations and change control",
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
    """One validated Markdown block consumed by the PDF renderer."""

    kind: str
    text: str
    level: int = 0


def repository_root(script_path: Path) -> Path:
    """Return the repository root for a renderer inside ``redis/``."""

    return script_path.resolve().parent.parent


def normalize_ascii(text: str) -> str:
    """Replace typography that built-in PDF fonts may render inconsistently."""

    normalized = text
    for source, replacement in ASCII_REPLACEMENTS.items():
        normalized = normalized.replace(source, replacement)
    return normalized


def parse_markdown(source_text: str) -> list[MarkdownBlock]:
    """Parse the intentionally limited Markdown subset used by the standard."""

    blocks: list[MarkdownBlock] = []
    paragraph_lines: list[str] = []
    code_lines: list[str] = []
    in_code = False

    def flush_paragraph() -> None:
        if paragraph_lines:
            blocks.append(MarkdownBlock("paragraph", " ".join(paragraph_lines)))
            paragraph_lines.clear()

    for raw_line in source_text.splitlines():
        line = normalize_ascii(raw_line.rstrip())
        if line.startswith("```"):
            if in_code:
                blocks.append(MarkdownBlock("code", "\n".join(code_lines)))
                code_lines.clear()
                in_code = False
            else:
                flush_paragraph()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line:
            flush_paragraph()
            continue
        if (
            line.startswith("  ")
            and not paragraph_lines
            and blocks
            and blocks[-1].kind in {"bullet", "numbered"}
        ):
            previous = blocks[-1]
            blocks[-1] = MarkdownBlock(
                previous.kind,
                f"{previous.text} {line.strip()}",
                previous.level,
            )
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
        if line.startswith("|"):
            raise ValueError("Markdown tables are not supported by this renderer")
        paragraph_lines.append(line.strip())

    flush_paragraph()
    if in_code:
        raise ValueError("unterminated fenced code block")
    if not blocks:
        raise ValueError("canonical source is empty")
    return blocks


def validate_blocks(blocks: Sequence[MarkdownBlock]) -> None:
    """Require the canonical source sections and safe client-neutral content."""

    headings = {block.text for block in blocks if block.kind == "heading"}
    missing = [heading for heading in REQUIRED_HEADINGS if heading not in headings]
    if missing:
        raise ValueError(f"canonical source is missing headings: {', '.join(missing)}")
    source_text = "\n".join(block.text for block in blocks).lower()
    forbidden = (
        "omi" + "celo",
        "vul" + "can",
        "haven" + "side",
        "om" + "redis",
        "om" + "web",
    )
    present = [token for token in forbidden if token in source_text]
    if present:
        raise ValueError(f"canonical public standard contains client identifiers: {present}")


def inline_markup(text: str) -> str:
    """Convert links, code, and bold spans into ReportLab paragraph markup."""

    normalized = normalize_ascii(text)
    token_pattern = re.compile(
        r"(\[[^\]]+\]\(https?://[^)]+\)|`[^`]+`|\*\*[^*]+\*\*)"
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
                f'<link href="{html.escape(target, quote=True)}" '
                f'color="#176B87">{html.escape(label)}</link>'
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


def group_list_blocks(
    blocks: Sequence[MarkdownBlock], start_index: int
) -> tuple[list[MarkdownBlock], int]:
    """Return one contiguous bullet or numbered-list group."""

    kind = blocks[start_index].kind
    grouped: list[MarkdownBlock] = []
    index = start_index
    while index < len(blocks) and blocks[index].kind == kind:
        grouped.append(blocks[index])
        index += 1
    return grouped, index


def wrap_code_block(text: str, width: int = 96) -> str:
    """Soft-wrap long code lines so no token can escape the printable frame."""

    wrapped_lines: list[str] = []
    for line in text.splitlines() or [""]:
        if len(line) <= width:
            wrapped_lines.append(line)
            continue
        continuation_indent = " " * min(len(line) - len(line.lstrip()) + 2, 12)
        wrapped_lines.extend(
            textwrap.wrap(
                line,
                width=width,
                subsequent_indent=continuation_indent,
                break_long_words=True,
                break_on_hyphens=False,
                replace_whitespace=False,
                drop_whitespace=False,
            )
        )
    return "\n".join(wrapped_lines)


def build_pdf(blocks: Sequence[MarkdownBlock], output_path: Path) -> None:
    """Build the final PDF atomically using ReportLab Platypus."""

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        KeepTogether,
        ListFlowable,
        ListItem,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        XPreformatted,
    )
    from reportlab.platypus.tableofcontents import TableOfContents

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
            fontSize=29,
            leading=34,
            textColor=colors.white,
            alignment=TA_LEFT,
            spaceAfter=12,
        )
    )
    styles.add(
        ParagraphStyle(
            "CoverSubtitle",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=13,
            leading=19,
            textColor=colors.HexColor("#D7EEF2"),
        )
    )
    styles.add(
        ParagraphStyle(
            "CoverTableHeader",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            textColor=colors.white,
        )
    )
    styles.add(
        ParagraphStyle(
            "BodyTextCustom",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9.35,
            leading=13.4,
            textColor=ink,
            spaceAfter=7,
            allowWidows=0,
            allowOrphans=0,
        )
    )
    styles.add(
        ParagraphStyle(
            "Heading1Custom",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=17,
            leading=21,
            textColor=navy,
            spaceBefore=16,
            spaceAfter=8,
            keepWithNext=True,
        )
    )
    styles.add(
        ParagraphStyle(
            "Heading2Custom",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=teal,
            spaceBefore=12,
            spaceAfter=5,
            keepWithNext=True,
        )
    )
    styles.add(
        ParagraphStyle(
            "CodeCustom",
            parent=styles["Code"],
            fontName="Courier",
            fontSize=6.75,
            leading=9.2,
            textColor=navy,
            backColor=pale,
            borderColor=light_rule,
            borderWidth=0.5,
            borderPadding=7,
            spaceBefore=4,
            spaceAfter=9,
            leftIndent=3,
            rightIndent=3,
        )
    )
    styles.add(
        ParagraphStyle(
            "ListCustom",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9.2,
            leading=13,
            textColor=ink,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            "TOCHeading",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=navy,
            spaceAfter=18,
        )
    )

    class SecurityStandardDocument(BaseDocTemplate):
        """Document template that registers headings with the TOC."""

        def afterFlowable(self, flowable: object) -> None:  # noqa: N802
            if not isinstance(flowable, Paragraph):
                return
            style_name = flowable.style.name
            if style_name not in {"Heading1Custom", "Heading2Custom"}:
                return
            level = 0 if style_name == "Heading1Custom" else 1
            plain_text = flowable.getPlainText()
            bookmark_digest = hashlib.sha256(plain_text.encode("utf-8")).hexdigest()[:12]
            bookmark = f"section-{level}-{self.page}-{bookmark_digest}"
            self.canv.bookmarkPage(bookmark)
            self.canv.addOutlineEntry(plain_text, bookmark, level=level, closed=False)
            self.notify("TOCEntry", (level, plain_text, self.page, bookmark))

    page_width, page_height = LETTER

    def draw_cover(canvas_object: canvas.Canvas, document: BaseDocTemplate) -> None:
        del document
        canvas_object.saveState()
        canvas_object.setFillColor(navy)
        canvas_object.rect(0, 0, page_width, page_height, fill=1, stroke=0)
        canvas_object.setFillColor(teal)
        canvas_object.rect(0, page_height - 0.22 * inch, page_width, 0.22 * inch, fill=1, stroke=0)
        canvas_object.setFillColor(aqua)
        canvas_object.circle(page_width - 0.85 * inch, 0.82 * inch, 0.22 * inch, fill=1, stroke=0)
        canvas_object.setFillColor(colors.HexColor("#2A596D"))
        canvas_object.circle(page_width - 0.44 * inch, 1.22 * inch, 0.11 * inch, fill=1, stroke=0)
        canvas_object.restoreState()

    def draw_body(canvas_object: canvas.Canvas, document: BaseDocTemplate) -> None:
        canvas_object.saveState()
        canvas_object.setStrokeColor(light_rule)
        canvas_object.setLineWidth(0.5)
        canvas_object.line(0.72 * inch, page_height - 0.56 * inch, page_width - 0.72 * inch, page_height - 0.56 * inch)
        canvas_object.setFillColor(muted)
        canvas_object.setFont("Helvetica", 7.5)
        canvas_object.drawString(0.72 * inch, page_height - 0.42 * inch, "REDIS SECURITY STANDARD")
        canvas_object.drawRightString(
            page_width - 0.72 * inch,
            page_height - 0.42 * inch,
            "PUBLIC - CLIENT-NEUTRAL BASELINE",
        )
        canvas_object.line(0.72 * inch, 0.52 * inch, page_width - 0.72 * inch, 0.52 * inch)
        canvas_object.drawString(0.72 * inch, 0.34 * inch, f"Version {DOCUMENT_VERSION} - {DOCUMENT_DATE}")
        canvas_object.drawRightString(page_width - 0.72 * inch, 0.34 * inch, f"Page {document.page}")
        canvas_object.restoreState()

    def draw_page(canvas_object: canvas.Canvas, document: BaseDocTemplate) -> None:
        """Draw the cover once, then the common body furniture on every page."""

        if document.page == 1:
            draw_cover(canvas_object, document)
        else:
            draw_body(canvas_object, document)

    body_frame = Frame(
        0.72 * inch,
        0.66 * inch,
        page_width - 1.44 * inch,
        page_height - 1.56 * inch,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        id="body",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=".redis-security-standard-",
        suffix=".pdf",
        dir=output_path.parent,
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)

    try:
        document = SecurityStandardDocument(
            str(temporary_path),
            pagesize=LETTER,
            title="Redis Security Standard",
            author="Michael Welter",
            subject="Client-neutral Redis listener and data-profile security standards",
            creator="config redis/build-security-standard-pdf.py",
            leftMargin=0.72 * inch,
            rightMargin=0.72 * inch,
            topMargin=0.66 * inch,
            bottomMargin=0.66 * inch,
        )
        document.addPageTemplates(
            [
                PageTemplate(
                    id="Document",
                    frames=[body_frame],
                    onPage=draw_page,
                ),
            ]
        )

        story: list[object] = []
        story.extend(
            [
                Spacer(1, 1.22 * inch),
                Paragraph("Redis Security Standard", styles["CoverTitle"]),
                Paragraph(
                    "Four secure single-server configurations across listener and data policy",
                    styles["CoverSubtitle"],
                ),
                Spacer(1, 0.46 * inch),
            ]
        )
        model_table = Table(
            [
                [
                    Paragraph("LISTENER", styles["CoverTableHeader"]),
                    Paragraph("CACHE", styles["CoverTableHeader"]),
                    Paragraph("DURABLE", styles["CoverTableHeader"]),
                ],
                [
                    Paragraph("LOCAL", styles["CoverTableHeader"]),
                    Paragraph(
                        "Loopback only. Disposable data, allkeys-lru, persistence off.",
                        styles["ListCustom"],
                    ),
                    Paragraph(
                        "Loopback only. Noeviction, AOF everysec, RDB and restore proof.",
                        styles["ListCustom"],
                    ),
                ],
                [
                    Paragraph("NETWORK", styles["CoverTableHeader"]),
                    Paragraph(
                        "Verified TLS and exact sources. Disposable, reproducible data only.",
                        styles["ListCustom"],
                    ),
                    Paragraph(
                        "Verified TLS and exact sources. Persistence plus restore proof.",
                        styles["ListCustom"],
                    ),
                ],
            ],
            colWidths=[1.05 * inch, 2.525 * inch, 2.525 * inch],
            hAlign="LEFT",
        )
        model_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), teal),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F2FAFA")),
                    ("BACKGROUND", (0, 1), (0, -1), navy),
                    ("TEXTCOLOR", (0, 1), (0, -1), colors.white),
                    ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#78AEBE")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B9D5DD")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.append(model_table)
        story.extend(
            [
                Spacer(1, 0.62 * inch),
                Paragraph(
                    "PUBLIC SCOPE",
                    ParagraphStyle(
                        "CoverLabel",
                        parent=styles["Normal"],
                        fontName="Helvetica-Bold",
                        fontSize=8,
                        textColor=aqua,
                        spaceAfter=6,
                    ),
                ),
                Paragraph(
                    "Reusable across clients and environments. Deployment names, CA identity, DNS suffixes, addresses, secrets, and evidence remain private inputs.",
                    styles["CoverSubtitle"],
                ),
                Spacer(1, 0.62 * inch),
                Paragraph(
                    f"Version {DOCUMENT_VERSION}<br/>{DOCUMENT_DATE}<br/>Redis Open Source 8.2.9 extended release<br/>Rocky Linux 9 and CentOS Stream 9 x86-64",
                    ParagraphStyle(
                        "CoverMeta",
                        parent=styles["Normal"],
                        fontName="Helvetica",
                        fontSize=9,
                        leading=14,
                        textColor=colors.HexColor("#C2DDE3"),
                    ),
                ),
                PageBreak(),
                Paragraph("Contents", styles["TOCHeading"]),
            ]
        )

        table_of_contents = TableOfContents()
        table_of_contents.levelStyles = [
            ParagraphStyle(
                "TOCLevel1",
                fontName="Helvetica-Bold",
                fontSize=9.5,
                leading=14,
                textColor=navy,
                leftIndent=0,
                firstLineIndent=0,
                spaceBefore=4,
            ),
            ParagraphStyle(
                "TOCLevel2",
                fontName="Helvetica",
                fontSize=8.5,
                leading=12,
                textColor=ink,
                leftIndent=14,
                firstLineIndent=0,
                spaceBefore=2,
            ),
        ]
        story.extend([table_of_contents, PageBreak()])

        index = 0
        while index < len(blocks):
            block = blocks[index]
            if block.kind == "heading":
                if block.level == 1:
                    index += 1
                    continue
                style = styles["Heading1Custom"] if block.level == 2 else styles["Heading2Custom"]
                story.append(Paragraph(inline_markup(block.text), style))
                index += 1
                continue
            if block.kind == "paragraph":
                story.append(Paragraph(inline_markup(block.text), styles["BodyTextCustom"]))
                index += 1
                continue
            if block.kind == "code":
                story.append(
                    XPreformatted(
                        html.escape(wrap_code_block(block.text)),
                        styles["CodeCustom"],
                    )
                )
                index += 1
                continue
            if block.kind in {"bullet", "numbered"}:
                grouped, next_index = group_list_blocks(blocks, index)
                list_items = [
                    ListItem(
                        Paragraph(inline_markup(item.text), styles["ListCustom"]),
                        leftIndent=12,
                    )
                    for item in grouped
                ]
                story.append(
                    ListFlowable(
                        list_items,
                        bulletType="bullet" if block.kind == "bullet" else "1",
                        start="-" if block.kind == "bullet" else 1,
                        leftIndent=18,
                        bulletFontName="Helvetica-Bold",
                        bulletFontSize=7.5,
                        bulletColor=teal,
                        spaceAfter=7,
                    )
                )
                index = next_index
                continue
            raise ValueError(f"unsupported Markdown block kind: {block.kind}")

        story.append(
            KeepTogether(
                [
                    Spacer(1, 10),
                    Table(
                        [[Paragraph(
                            "Acceptance reminder: restricted-network deployment is incomplete until both an allowed-source test and a denied-source test are recorded in the private evidence set.",
                            styles["BodyTextCustom"],
                        )]],
                        colWidths=[6.1 * inch],
                        style=TableStyle(
                            [
                                ("BACKGROUND", (0, 0), (-1, -1), pale),
                                ("BOX", (0, 0), (-1, -1), 0.8, teal),
                                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                                ("TOPPADDING", (0, 0), (-1, -1), 8),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                            ]
                        ),
                    ),
                ]
            )
        )

        document.multiBuild(
            story,
            canvasmaker=lambda *args, **kwargs: canvas.Canvas(
                *args, **(kwargs | {"invariant": 1})
            ),
        )
        os.replace(temporary_path, output_path)
        output_path.chmod(0o644)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def build_argument_parser(default_source: Path, default_output: Path) -> argparse.ArgumentParser:
    """Create the command-line parser with deterministic path defaults."""

    parser = argparse.ArgumentParser(
        description="Render the canonical Redis security standard as PDF."
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
    """Validate inputs and optionally render the final PDF."""

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
    blocks = parse_markdown(source_path.read_text(encoding="utf-8"))
    validate_blocks(blocks)
    if arguments.check:
        print(f"Validated canonical Redis security standard: {source_path}")
        return 0
    build_pdf(blocks, output_path)
    print(f"Rendered Redis security standard PDF: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
