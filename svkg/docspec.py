"""Read the v0.2 specification .docx into a raw, order-preserving structure.

Shared by the extractor (which builds the Appendix A files) and the checker
(which re-reads the document independently and diffs it against those files).
Nothing here interprets the content; it only locates tables and paragraphs.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "docs" / "Student_Visa_Knowledge_Graph_POC_Consolidated_Export_v0.2.docx"


@dataclass
class Block:
    kind: str                     # "para" | "table"
    style: str = ""
    text: str = ""
    rows: list[list[str]] = field(default_factory=list)
    heading: str = ""             # nearest Heading 1
    subheading: str = ""          # nearest Heading 2 (reset at each Heading 1)
    table_index: int | None = None


def _cell(c) -> str:
    return " ".join(c.text.split()) if "\n" not in c.text else " / ".join(
        " ".join(line.split()) for line in c.text.split("\n") if line.strip())


def read_blocks(path: Path = SPEC_PATH) -> list[Block]:
    d = docx.Document(str(path))
    out: list[Block] = []
    h1 = h2 = ""
    ti = 0
    for el in d.element.body.iterchildren():
        tag = el.tag.split("}")[1]
        if tag == "p":
            p = Paragraph(el, d)
            style = p.style.name if p.style is not None else ""
            text = p.text
            if not text.strip():
                continue
            if style == "Heading 1":
                h1, h2 = text.strip(), ""
            elif style == "Heading 2":
                h2 = text.strip()
            out.append(Block("para", style, text, heading=h1, subheading=h2))
        elif tag == "tbl":
            t = Table(el, d)
            rows = [[_cell(c) for c in r.cells] for r in t.rows]
            out.append(Block("table", rows=rows, heading=h1, subheading=h2, table_index=ti))
            ti += 1
    return out


def sha256(path: Path = SPEC_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Spec:
    """Convenience accessors over the raw blocks."""

    def __init__(self, path: Path = SPEC_PATH):
        self.path = path
        self.blocks = read_blocks(path)

    # -- tables -------------------------------------------------------------
    def tables(self) -> list[Block]:
        return [b for b in self.blocks if b.kind == "table"]

    def table_by_header(self, first_header: str, heading_prefix: str | None = None,
                        subheading_prefix: str | None = None) -> Block:
        hits = [b for b in self.tables() if b.rows and b.rows[0][0] == first_header
                and (heading_prefix is None or b.heading.startswith(heading_prefix))
                and (subheading_prefix is None or b.subheading.startswith(subheading_prefix))]
        if len(hits) != 1:
            raise LookupError(f"expected one table headed {first_header!r} under "
                              f"{heading_prefix!r}/{subheading_prefix!r}, found {len(hits)}")
        return hits[0]

    def rule_tables(self) -> list[Block]:
        """Every table whose first header cell is 'Rule ID' (verified catalogue)."""
        return [b for b in self.tables() if b.rows and b.rows[0][0] == "Rule ID"]

    # -- paragraphs ---------------------------------------------------------
    def paras(self, heading_prefix: str, subheading_prefix: str | None = None,
              style: str | None = None) -> list[Block]:
        return [b for b in self.blocks if b.kind == "para"
                and b.heading.startswith(heading_prefix)
                and (subheading_prefix is None or b.subheading.startswith(subheading_prefix))
                and (style is None or b.style == style)
                and not b.style.startswith("Heading")]

    def bullets(self, heading_prefix: str) -> list[str]:
        return [b.text.strip() for b in self.paras(heading_prefix, style="List Bullet")]

    def template(self, subheading_prefix: str) -> tuple[str, str]:
        """Return (name, body) of a 'No Spacing' template paragraph under a Heading 2."""
        ps = [b for b in self.blocks if b.kind == "para" and b.style == "No Spacing"
              and b.subheading.split(" ")[0] == subheading_prefix]
        if len(ps) != 1:
            raise LookupError(f"template {subheading_prefix!r}: found {len(ps)}")
        text = ps[0].text
        return text, text

    def json_template(self, subheading_prefix: str) -> tuple[str, dict]:
        """Appendix F templates are 'name:' on the first line followed by JSON."""
        text, _ = self.template(subheading_prefix)
        first, _, rest = text.partition("\n")
        return first.strip().rstrip(":"), json.loads(rest)

    def scope_notice(self) -> str:
        t = self.tables()[0]
        return t.rows[0][0]

    def version_line(self) -> str:
        for b in self.blocks[:6]:
            if b.kind == "para" and b.text.startswith("Version"):
                return b.text.strip()
        raise LookupError("version line")
